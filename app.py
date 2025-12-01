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

# === FUNÇÕES DE INTEGRAÇÃO (Backend) ===

def get_trakt_profile_data(username, content_type="movies"):
    """Baixa perfil profundo: Histórico + Notas (Loved/Hated)"""
    headers = {'Content-Type': 'application/json', 'trakt-api-version': '2', 'trakt-api-key': TRAKT_CLIENT_ID}
    data = {"history": [], "loved": [], "liked": [], "hated": [], "watched_ids": []}
    
    t_type = "shows" if content_type == "tv" else "movies"
    item_key = 'show' if content_type == "tv" else 'movie'

    try:
        # 1. Watched IDs
        r_watched = requests.get(f"https://api.trakt.tv/users/{username}/watched/{t_type}", headers=headers)
        if r_watched.status_code == 200:
            data["watched_ids"] = [i[item_key]['ids']['tmdb'] for i in r_watched.json() if i[item_key]['ids'].get('tmdb')]

        # 2. Histórico
        r_hist = requests.get(f"https://api.trakt.tv/users/{username}/history/{t_type}?limit=10", headers=headers)
        if r_hist.status_code == 200:
            data["history"] = [i[item_key]['title'] for i in r_hist.json()]

        # 3. Ratings (Notas)
        r_ratings = requests.get(f"https://api.trakt.tv/users/{username}/ratings/{t_type}?limit=50", headers=headers)
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
    if not data: return ""
    c = ""
    if data.get('loved'): c += f"AMOU (9-10): {', '.join(data['loved'][:15])}. "
    if data.get('liked'): c += f"CURTIU (7-8): {', '.join(data['liked'][:10])}. "
    if data.get('hated'): c += f"ODIOU (1-5): {', '.join(data['hated'][:15])}. "
    return c

def explain_choice(title, context_str, user_query, overview, rating):
    prompt = f"""
    Atue como um crítico de cinema.
    PERFIL: {context_str}
    PEDIDO: "{user_query}"
    RECOMENDAÇÃO: "{title}" (Nota: {rating}/10).
    SINOPSE: {overview}
    TAREFA: Em UMA frase, explique por que essa obra encaixa no perfil e no pedido.
    """
    try:
        model = genai.GenerativeModel('models/gemini-2.0-flash')
        return model.generate_content(prompt).text.strip()
    except: return "Recomendação baseada no seu perfil."

# === FUNÇÕES DE PERSISTÊNCIA (DASHBOARD) ===

def load_user_dashboard(username):
    response = supabase.table("user_dashboards").select("*").eq("trakt_username", username).execute()
    return response.data[0] if response.data else None

def save_user_dashboard(username, curated_list, prefs):
    data = {
        "trakt_username": username,
        "curated_list": curated_list,
        "preferences": prefs,
        "updated_at": datetime.now().isoformat()
    }
    supabase.table("user_dashboards").upsert(data).execute()

# === INTERFACE ===

st.sidebar.title("🍿 CineGourmet")

# --- SIDEBAR GLOBAL (Configurações valem para as duas abas) ---
with st.sidebar:
    st.header("1. Configurações")
    
    # Tipo de Conteúdo Global
    c_type = st.radio("Tipo de Conteúdo", ["Filmes", "Séries"], horizontal=True)
    api_type = "tv" if c_type == "Séries" else "movie"
    db_func = "match_tv_shows" if c_type == "Séries" else "match_movies"
    
    # Trakt Login Global
    st.markdown("---")
    username = st.text_input("Usuário Trakt (Login):", placeholder="ex: lscastro")
    
    if st.button("🔄 Sincronizar Perfil"):
        if username:
            with st.spinner("Baixando dados do Trakt..."):
                # Carrega no Session State para usar em todo o app
                st.session_state['trakt_data'] = get_trakt_profile_data(username, api_type)
                st.success("Perfil Carregado!")
        else:
            st.warning("Digite um usuário.")
            
    # Mostra status do perfil
    if 'trakt_data' in st.session_state:
        d = st.session_state['trakt_data']
        st.caption(f"✅ Perfil Ativo: {len(d['loved'])} amados, {len(d['watched_ids'])} vistos.")
    
    st.markdown("---")
    st.subheader("📺 Meus Streamings")
    services_list = ["Netflix", "Amazon Prime Video", "Disney Plus", "Max", "Apple TV Plus", "Globoplay"]
    my_services = st.multiselect("O que você assina?", services_list, default=services_list)
    threshold = st.slider("Ousadia", 0.0, 1.0, 0.45)

# Navegação entre Abas
page = st.radio("Modo de Navegação", ["🔍 Busca Rápida (Chat)", "💎 Minha Curadoria Fixa (30)"], horizontal=True, label_visibility="collapsed")
st.divider()

# ==============================================================================
# PÁGINA 1: BUSCA RÁPIDA (INTEGRADA COM TRAKT)
# ==============================================================================
if page == "🔍 Busca Rápida (Chat)":
    st.title(f"🔍 Busca Inteligente: {c_type}")
    
    # Verifica contexto
    context_str = ""
    blocked_ids = []
    
    if 'trakt_data' in st.session_state:
        context_str = build_context_string(st.session_state['trakt_data'])
        blocked_ids = st.session_state['trakt_data']['watched_ids']
        st.info(f"🧠 Modo Personalizado Ativo: Usando o gosto de **{username}** para filtrar resultados.")
    else:
        st.warning("⚠️ Modo Genérico: Sincronize o Trakt na barra lateral para recomendações personalizadas.")

    query = st.text_area("O que você quer ver agora?", placeholder="Ex: Sci-fi cyberpunk com final triste...")
    
    if st.button("🚀 Buscar", type="primary"):
        if not query:
            st.warning("Digite algo!")
        else:
            final_prompt = f"Pedido: {query}. Contexto do Usuário: {context_str}"
            
            with st.spinner("A IA está pensando..."):
                # 1. Embed
                vector = genai.embed_content(model="models/text-embedding-004", content=final_prompt)['embedding']
                
                # 2. Busca (Filtrando assistidos)
                resp = supabase.rpc(db_func, {
                    "query_embedding": vector, 
                    "match_threshold": threshold, 
                    "match_count": 50, # Margem para filtro
                    "filter_ids": blocked_ids
                }).execute()
                
                results = []
                # 3. Filtro Streaming
                if resp.data:
                    for m in resp.data:
                        if len(results) >= 5: break
                        is_ok, flat, rent = get_watch_providers(m['id'], api_type, my_services)
                        if is_ok:
                            m['providers'] = flat
                            m['rent'] = rent
                            results.append(m)
                
                # 4. Exibição
                if not results:
                    st.error("Nada encontrado nos seus streamings/critérios.")
                else:
                    for item in results:
                        c1, c2 = st.columns([1, 4])
                        with c1:
                            if item['poster_path']: st.image(TMDB_IMAGE + item['poster_path'], use_container_width=True)
                            if item.get('providers'):
                                cols = st.columns(len(item['providers']))
                                for i, p in enumerate(item['providers']):
                                    with cols[i]: st.image(TMDB_LOGO + p['logo_path'], width=30)
                        
                        with c2:
                            rating = float(item.get('vote_average', 0) or 0)
                            stars = "⭐" * int(round(rating/2))
                            st.markdown(f"### {item['title']} | {rating:.1f}/10 {stars}")
                            
                            match = int(item['similarity']*100)
                            st.progress(match, text=f"Match: {match}%")
                            
                            # Explicação usando o perfil
                            expl = explain_choice(item['title'], context_str if context_str else "Geral", query, item['overview'], rating)
                            st.success(f"💡 {expl}")
                            
                            # Botões
                            b1, b2 = st.columns(2)
                            trailer = get_trailer_url(item['id'], api_type)
                            if trailer: b1.link_button("▶️ Trailer", trailer)
                            b2.link_button("📝 Trakt", get_trakt_url(item['id'], api_type))
                            
                            with st.expander("Sinopse"): st.write(item['overview'])
                        st.divider()

# ==============================================================================
# PÁGINA 2: CURADORIA VIP (PERSISTENTE)
# ==============================================================================
elif page == "💎 Minha Curadoria Fixa (30)":
    st.title(f"💎 Curadoria VIP: {c_type}")
    
    if not username:
        st.error("Por favor, digite seu Usuário Trakt na barra lateral para acessar sua lista.")
    else:
        # Carrega Dashboard
        dashboard = load_user_dashboard(username)
        
        # Botão de Gerar/Atualizar
        btn_text = "🔄 Atualizar Lista" if dashboard else "✨ Gerar Lista VIP"
        if st.button(btn_text):
            if 'trakt_data' not in st.session_state:
                st.error("Sincronize o perfil na barra lateral primeiro!")
            else:
                with st.spinner("Gerando 30 recomendações baseadas no seu DNA..."):
                    context_str = build_context_string(st.session_state['trakt_data'])
                    blocked_ids = st.session_state['trakt_data']['watched_ids']
                    
                    prompt = f"Analise este perfil: {context_str}. Encontre 30 obras-primas OBRIGATÓRIAS (Hidden Gems, Cults, Alta Nota) que ele AINDA NÃO VIU."
                    vector = genai.embed_content(model="models/text-embedding-004", content=prompt)['embedding']
                    
                    resp = supabase.rpc(db_func, {
                        "query_embedding": vector, 
                        "match_threshold": threshold, 
                        "match_count": 100,
                        "filter_ids": blocked_ids
                    }).execute()
                    
                    final_list = []
                    if resp.data:
                        for m in resp.data:
                            if len(final_list) >= 30: break
                            is_ok, flat, rent = get_watch_providers(m['id'], api_type, my_services)
                            if is_ok:
                                m['providers_flat'] = flat
                                m['trailer'] = get_trailer_url(m['id'], api_type)
                                m['trakt_url'] = get_trakt_url(m['id'], api_type)
                                final_list.append(m)
                    
                    if final_list:
                        save_user_dashboard(username, final_list, {"type": c_type})
                        st.rerun()
        
        # Exibição da Grade
        if dashboard and dashboard.get('curated_list'):
            st.divider()
            st.caption(f"Última atualização: {datetime.fromisoformat(dashboard['updated_at']).strftime('%d/%m %H:%M')}")
            
            items = dashboard['curated_list']
            # Filtra tipo se o usuário mudou na sidebar (opcional, mas bom pra UI)
            # Mas a lista salva tem um tipo fixo. Ideal é avisar.
            
            cols = st.columns(3)
            for idx, item in enumerate(items):
                with cols[idx % 3]:
                    with st.container(border=True):
                        if item['poster_path']: st.image(TMDB_IMAGE + item['poster_path'], use_container_width=True)
                        st.markdown(f"**{item['title']}**")
                        rating = float(item.get('vote_average', 0) or 0)
                        st.caption(f"{rating:.1f}/10 ⭐")
                        
                        if item.get('providers_flat'):
                            p_cols = st.columns(len(item['providers_flat']))
                            for i, p in enumerate(item['providers_flat']):
                                if i<4: 
                                    with p_cols[i]: st.image(TMDB_LOGO + p['logo_path'], width=20)
                        
                        with st.expander("Detalhes"):
                            st.write(item['overview'])
                            if item.get('trailer'): st.link_button("Trailer", item['trailer'])
                            if item.get('trakt_url'): st.link_button("Trakt", item['trakt_url'])
