import streamlit as st
import google.generativeai as genai
import requests
import pandas as pd
from supabase import create_client
from datetime import datetime
import json

# === CONFIGURAÇÃO E SEGREDOS ===
try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
    TRAKT_CLIENT_ID = st.secrets["TRAKT_CLIENT_ID"]
    TMDB_API_KEY = st.secrets["TMDB_API_KEY"]
except:
    st.error("🚨 Configure os Secrets no Streamlit!")
    st.stop()

genai.configure(api_key=GOOGLE_API_KEY)
st.set_page_config(page_title="CineGourmet Ultimate", page_icon="🍿", layout="wide")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
TMDB_IMAGE = "https://image.tmdb.org/t/p/w500"
TMDB_LOGO = "https://image.tmdb.org/t/p/original"

# === FUNÇÕES DE SUPORTE (TRAKT, TMDB, ETC) ===
# (Mantivemos as mesmas funções de antes, mas organizadas)

def get_trakt_profile_data(username, content_type="movies"):
    headers = {'Content-Type': 'application/json', 'trakt-api-version': '2', 'trakt-api-key': TRAKT_CLIENT_ID}
    data = {"history": [], "loved": [], "liked": [], "hated": [], "watched_ids": []}
    t_type = "shows" if content_type == "tv" else "movies"
    item_key = 'show' if content_type == "tv" else 'movie'
    try:
        r_watched = requests.get(f"https://api.trakt.tv/users/{username}/watched/{t_type}", headers=headers)
        if r_watched.status_code == 200:
            data["watched_ids"] = [i[item_key]['ids']['tmdb'] for i in r_watched.json() if i[item_key]['ids'].get('tmdb')]
        r_ratings = requests.get(f"https://api.trakt.tv/users/{username}/ratings/{t_type}?limit=100", headers=headers) # Pegando mais ratings
        if r_ratings.status_code == 200:
            for item in r_ratings.json():
                title = item[item_key]['title']
                rating = item['rating']
                entry = f"{title} ({rating}/10)"
                if rating >= 9: data["loved"].append(entry)
                elif rating >= 7: data["liked"].append(entry)
                elif rating <= 5: data["hated"].append(entry)
    except: pass
    return data

def get_watch_providers(content_id, content_type, filter_providers=None):
    url = f"https://api.themoviedb.org/3/{content_type}/{content_id}/watch/providers?api_key={TMDB_API_KEY}"
    try:
        r = requests.get(url)
        data = r.json()
        if 'results' in data and 'BR' in data['results']:
            br = data['results']['BR']
            flatrate = br.get('flatrate', [])
            rent = br.get('rent', [])
            if filter_providers:
                avail_names = [p['provider_name'] for p in flatrate]
                is_available = any(my_prov in avail_names for my_prov in filter_providers)
                return is_available, flatrate, rent
            return True, flatrate, rent
    except: pass
    return False, [], []

def get_trailer_url(content_id, content_type):
    url = f"https://api.themoviedb.org/3/{content_type}/{content_id}/videos?api_key={TMDB_API_KEY}&language=pt-BR"
    try:
        r = requests.get(url)
        data = r.json()
        if 'results' in data:
            for v in data['results']:
                if v['site'] == 'YouTube' and v['type'] == 'Trailer': return f"https://www.youtube.com/watch?v={v['key']}"
            # Fallback EN
            url_en = f"https://api.themoviedb.org/3/{content_type}/{content_id}/videos?api_key={TMDB_API_KEY}&language=en-US"
            r_en = requests.get(url_en)
            for v in r_en.json().get('results', []):
                if v['site'] == 'YouTube' and v['type'] == 'Trailer': return f"https://www.youtube.com/watch?v={v['key']}"
    except: pass
    return None

def get_trakt_url(content_id, content_type):
    type_slug = "movie" if content_type == "movie" else "show"
    return f"https://trakt.tv/search/tmdb/{content_id}?id_type={type_slug}"

def build_context_string(data):
    c = ""
    if data['loved']: c += f"AMOU (9-10): {', '.join(data['loved'][:15])}. "
    if data['liked']: c += f"CURTIU (7-8): {', '.join(data['liked'][:10])}. "
    if data['hated']: c += f"ODIOU (1-5): {', '.join(data['hated'][:15])}. "
    return c

# === FUNÇÕES DE DASHBOARD (NOVAS) ===

def load_user_dashboard(username):
    """Carrega a lista salva do Supabase"""
    response = supabase.table("user_dashboards").select("*").eq("trakt_username", username).execute()
    if response.data:
        return response.data[0]
    return None

def save_user_dashboard(username, curated_list, prefs):
    """Salva a lista gerada no Supabase"""
    data = {
        "trakt_username": username,
        "curated_list": curated_list,
        "preferences": prefs,
        "updated_at": datetime.now().isoformat()
    }
    supabase.table("user_dashboards").upsert(data).execute()

def generate_curated_list(username, content_type, providers, threshold):
    """Gera a lista pesada de 30 itens"""
    
    # 1. Analisa Perfil
    api_type = "tv" if content_type == "tv" else "movie"
    db_func = "match_tv_shows" if content_type == "tv" else "match_movies"
    
    profile = get_trakt_profile_data(username, api_type)
    if not profile['loved'] and not profile['liked']:
        return None, "Perfil Trakt sem dados suficientes (notas/histórico)."
    
    context = build_context_string(profile)
    
    # 2. Prompt Especialista
    prompt = f"""
    Analise este perfil de espectador PROFUNDAMENTE:
    {context}
    
    Identifique os padrões sutis (diretores, ritmo, temas).
    Eu preciso encontrar OBRAS-PRIMAS que ele ainda não viu.
    Foque em "Hidden Gems" (Jóias Ocultas) e clássicos cult que combinam com o gosto dele.
    Evite o óbvio.
    """
    
    # 3. Embedding
    vector = genai.embed_content(model="models/text-embedding-004", content=prompt)['embedding']
    
    # 4. Busca no Banco (Buscamos 100 para filtrar e sobrar 30 bons)
    response = supabase.rpc(db_func, {
        "query_embedding": vector,
        "match_threshold": threshold, 
        "match_count": 80, # Busca bastante para ter margem
        "filter_ids": profile['watched_ids']
    }).execute()
    
    final_list = []
    
    # 5. Filtragem
    if response.data:
        for item in response.data:
            if len(final_list) >= 30: break # Meta: 30 itens
            
            is_ok, flat, rent = get_watch_providers(item['id'], api_type, providers)
            
            if is_ok: # Se passou no filtro de streaming (ou se não tiver filtro)
                # Enriquecendo o item para salvar no JSON
                item['providers_flat'] = flat
                item['providers_rent'] = rent
                item['trailer'] = get_trailer_url(item['id'], api_type)
                item['trakt_url'] = get_trakt_url(item['id'], api_type)
                final_list.append(item)
    
    return final_list, "Sucesso"

# === INTERFACE ===

st.sidebar.title("🍿 CineGourmet")
page = st.sidebar.radio("Navegação", ["🔍 Busca Rápida", "💎 Minha Curadoria VIP"])

# ==============================================================================
# PÁGINA 1: BUSCA RÁPIDA (O código antigo, simplificado para caber aqui)
# ==============================================================================
if page == "🔍 Busca Rápida":
    st.title("🔍 Busca Instantânea")
    
    # Config rápida
    c_type = st.radio("Tipo", ["Filmes", "Séries"], horizontal=True)
    api_type = "tv" if c_type == "Séries" else "movie"
    db_func = "match_tv_shows" if c_type == "Séries" else "match_movies"
    
    query = st.text_area("O que você quer ver agora?", placeholder="Ex: Terror psicológico anos 90...")
    
    if st.button("Buscar Agora"):
        if not query:
            st.warning("Digite algo!")
        else:
            with st.spinner("Buscando..."):
                vector = genai.embed_content(model="models/text-embedding-004", content=query)['embedding']
                resp = supabase.rpc(db_func, {"query_embedding": vector, "match_threshold": 0.45, "match_count": 10}).execute()
                
                if resp.data:
                    for m in resp.data:
                        c1, c2 = st.columns([1,4])
                        with c1: 
                            if m['poster_path']: st.image(TMDB_IMAGE + m['poster_path'])
                        with c2:
                            st.subheader(m['title'])
                            st.write(m['overview'])
                            # Trailer rapidinho
                            trailer = get_trailer_url(m['id'], api_type)
                            if trailer: st.link_button("Trailer", trailer)
                        st.divider()

# ==============================================================================
# PÁGINA 2: CURADORIA VIP (O NOVO RECURSO)
# ==============================================================================
elif page == "💎 Minha Curadoria VIP":
    st.title("💎 Sua Curadoria Personalizada")
    st.markdown("Uma lista de 30 recomendações feita sob medida e **salva** para você.")
    
    # LOGIN VIRTUAL
    username = st.text_input("Digite seu Usuário Trakt para entrar:", placeholder="ex: lscastro")
    
    if username:
        # Carrega dados salvos
        dashboard = load_user_dashboard(username)
        
        if dashboard:
            last_update = datetime.fromisoformat(dashboard['updated_at']).strftime("%d/%m/%Y às %H:%M")
            st.success(f"Bem-vindo de volta, **{username}**! Lista atualizada em: {last_update}")
        else:
            st.info(f"Olá **{username}**. Você ainda não tem uma lista gerada.")

        # CONFIGURAÇÕES DA LISTA
        with st.expander("⚙️ Configurar Critérios da Lista", expanded=not dashboard):
            col1, col2 = st.columns(2)
            with col1:
                pref_type = st.radio("Prefiro receber:", ["Filmes", "Séries"], key="vip_type")
            with col2:
                services = ["Netflix", "Amazon Prime Video", "Disney Plus", "Max", "Apple TV Plus", "Globoplay"]
                pref_services = st.multiselect("Tenho acesso a:", services, default=services, key="vip_services")
            
            pref_threshold = st.slider("Nível de Ousadia (Curadoria)", 0.0, 1.0, 0.45, key="vip_thresh")

        # BOTÃO DE GERAR/ATUALIZAR
        btn_text = "🔄 Atualizar Minha Lista" if dashboard else "✨ Gerar Primeira Lista"
        
        if st.button(btn_text, type="primary"):
            with st.spinner("⏳ Analisando seu perfil profundo, notas, histórico e calculando 30 recomendações... (Isso leva uns 15 segs)"):
                
                api_type_code = "tv" if pref_type == "Séries" else "movie"
                
                new_list, status = generate_curated_list(username, api_type_code, pref_services, pref_threshold)
                
                if new_list:
                    save_user_dashboard(username, new_list, {"type": pref_type, "services": pref_services})
                    st.toast("Lista Salva com Sucesso!", icon="💾")
                    st.rerun() # Recarrega a página para mostrar a lista nova
                else:
                    st.error(f"Erro: {status}")

        # EXIBIÇÃO DA LISTA (GRID)
        if dashboard and dashboard.get('curated_list'):
            st.divider()
            st.subheader(f"Top 30 {dashboard['preferences']['type']} para Você")
            
            # Grid de 3 colunas
            items = dashboard['curated_list']
            
            # Filtro local rápido
            sort_order = st.selectbox("Ordenar por:", ["Match (IA)", "Nota (TMDB)", "Populares"])
            if sort_order == "Nota (TMDB)":
                items.sort(key=lambda x: float(x.get('vote_average') or 0), reverse=True)
            elif sort_order == "Populares":
                items.sort(key=lambda x: float(x.get('popularity') or 0), reverse=True)
            
            # Exibição em Grade
            cols = st.columns(3)
            for idx, item in enumerate(items):
                with cols[idx % 3]:
                    # Card do Filme
                    with st.container(border=True):
                        # Poster
                        if item['poster_path']:
                            st.image(TMDB_IMAGE + item['poster_path'], use_container_width=True)
                        
                        # Título e Nota
                        rating = float(item.get('vote_average') or 0)
                        stars = "⭐" * int(round(rating/2))
                        st.markdown(f"**{item['title']}**")
                        st.caption(f"{rating:.1f}/10 {stars}")
                        
                        # Onde assistir (ícones)
                        if item.get('providers_flat'):
                            prov_cols = st.columns(len(item['providers_flat']))
                            for i, p in enumerate(item['providers_flat']):
                                if i < 4: # Limita a 4 ícones pra não quebrar
                                    with prov_cols[i]: st.image(TMDB_LOGO + p['logo_path'], width=25)
                        
                        # Sinopse (Expander)
                        with st.expander("Ver detalhes"):
                            st.write(item['overview'])
                            c_btn1, c_btn2 = st.columns(2)
                            if item.get('trailer'): 
                                c_btn1.link_button("Trailer", item['trailer'])
                            if item.get('trakt_url'):
                                c_btn2.link_button("Trakt", item['trakt_url'])
