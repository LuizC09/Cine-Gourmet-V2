import streamlit as st
import google.generativeai as genai
import requests
import pandas as pd
from supabase import create_client

# === CONFIGURAÇÃO E SEGREDOS ===
try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
    TRAKT_CLIENT_ID = st.secrets["TRAKT_CLIENT_ID"]
    TMDB_API_KEY = st.secrets["TMDB_API_KEY"]
except:
    st.error("🚨 Configure os Secrets no Streamlit! (Falta TMDB_API_KEY ou outros)")
    st.stop()

genai.configure(api_key=GOOGLE_API_KEY)
st.set_page_config(page_title="CineGourmet Ultimate", page_icon="🍿", layout="wide")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
TMDB_IMAGE = "https://image.tmdb.org/t/p/w500"
TMDB_LOGO = "https://image.tmdb.org/t/p/original"

# === FUNÇÕES DE INTEGRAÇÃO (TRAKT, TMDB, YOUTUBE) ===

def get_trakt_profile_data(username, content_type="movies"):
    """
    Baixa o perfil do Trakt separando o que AMOU (9-10), CURTIU (7-8) e ODIOU (1-5).
    """
    headers = {'Content-Type': 'application/json', 'trakt-api-version': '2', 'trakt-api-key': TRAKT_CLIENT_ID}
    data = {"history": [], "loved": [], "liked": [], "hated": [], "watched_ids": []}
    
    t_type = "shows" if content_type == "tv" else "movies"
    item_key = 'show' if content_type == "tv" else 'movie'

    try:
        # 1. Watched IDs (Para não recomendar repetido)
        r_watched = requests.get(f"https://api.trakt.tv/users/{username}/watched/{t_type}", headers=headers)
        if r_watched.status_code == 200:
            data["watched_ids"] = [i[item_key]['ids']['tmdb'] for i in r_watched.json() if i[item_key]['ids'].get('tmdb')]

        # 2. Histórico Recente
        r_hist = requests.get(f"https://api.trakt.tv/users/{username}/history/{t_type}?limit=10", headers=headers)
        if r_hist.status_code == 200:
            data["history"] = [i[item_key]['title'] for i in r_hist.json()]

        # 3. Avaliações (Para entender o gosto)
        r_ratings = requests.get(f"https://api.trakt.tv/users/{username}/ratings/{t_type}?limit=50", headers=headers)
        if r_ratings.status_code == 200:
            for item in r_ratings.json():
                title = item[item_key]['title']
                rating = item['rating']
                
                entry = f"{title} ({rating}/10)"
                if rating >= 9: data["loved"].append(entry)
                elif rating >= 7: data["liked"].append(entry)
                elif rating <= 5: data["hated"].append(entry)

    except Exception as e: pass
    return data

def get_watch_providers(content_id, content_type, filter_providers=None):
    """Verifica disponibilidade em streamings no Brasil"""
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
    """Busca link do trailer no YouTube"""
    url = f"https://api.themoviedb.org/3/{content_type}/{content_id}/videos?api_key={TMDB_API_KEY}&language=pt-BR"
    try:
        r = requests.get(url)
        data = r.json()
        if 'results' in data:
            for v in data['results']:
                if v['site'] == 'YouTube' and v['type'] == 'Trailer':
                    return f"https://www.youtube.com/watch?v={v['key']}"
            
            # Fallback para Inglês
            url_en = f"https://api.themoviedb.org/3/{content_type}/{content_id}/videos?api_key={TMDB_API_KEY}&language=en-US"
            r_en = requests.get(url_en)
            data_en = r_en.json()
            for v in data_en.get('results', []):
                if v['site'] == 'YouTube' and v['type'] == 'Trailer':
                    return f"https://www.youtube.com/watch?v={v['key']}"
    except: pass
    return None

def get_trakt_url(content_id, content_type):
    """Link profundo para o Trakt"""
    type_slug = "movie" if content_type == "movie" else "show"
    return f"https://trakt.tv/search/tmdb/{content_id}?id_type={type_slug}"

def explain_choice(title, context_str, user_query, overview, rating):
    """Usa IA para explicar a escolha"""
    prompt = f"""
    Atue como um curador de cinema perspicaz.
    PERFIL DO USUÁRIO: {context_str}
    PEDIDO: "{user_query}"
    RECOMENDAÇÃO: "{title}" (Nota TMDB: {rating}/10).
    SINOPSE: {overview}

    TAREFA: Explique em UMA frase por que essa obra se encaixa no perfil.
    Se a nota for alta, mencione que é aclamado. Se o usuário odeia algo parecido, diferencie este filme.
    """
    try:
        model = genai.GenerativeModel('models/gemini-2.0-flash')
        return model.generate_content(prompt).text.strip()
    except: return "Recomendação baseada na análise do seu perfil."

# === INTERFACE ===

st.title("🍿 CineGourmet Ultimate")

# --- SIDEBAR ---
with st.sidebar:
    st.header("⚙️ Configuração")
    
    # 1. ESCOLHA O TIPO
    content_type_label = st.radio("O que vamos ver?", ["Filmes 🎬", "Séries 📺"])
    is_tv = "Séries" in content_type_label
    api_type = "tv" if is_tv else "movie"
    db_func = "match_tv_shows" if is_tv else "match_movies"
    
    st.divider()
    
    # 2. MODO CASAL/SOLO
    mode = st.radio("Modo", ["Solo", "Casal"])
    
    user_a = st.text_input("Seu Trakt User", key="user_a")
    user_b = None if mode == "Solo" else st.text_input("Parceiro(a)", key="user_b")
    
    if st.button("🔄 Sincronizar (Ler Notas)"):
        with st.spinner("Analisando notas no Trakt..."):
            if user_a: st.session_state['data_a'] = get_trakt_profile_data(user_a, api_type)
            if user_b: st.session_state['data_b'] = get_trakt_profile_data(user_b, api_type)
            st.success("Perfil atualizado!")
    
    st.divider()
    st.subheader("📺 Streamings")
    services = ["Netflix", "Amazon Prime Video", "Disney Plus", "Max", "Apple TV Plus", "Globoplay"]
    my_services = st.multiselect("O que você assina?", services)
    threshold = st.slider("Ousadia", 0.0, 1.0, 0.45)

# --- CONSTRUÇÃO DO CONTEXTO ---
context = ""
blocked_ids = []

def build_context_string(data, label):
    c = ""
    if data['loved']: c += f"{label} AMOU (9-10): {', '.join(data['loved'])}. "
    if data['liked']: c += f"{label} CURTIU (7-8): {', '.join(data['liked'])}. "
    if data['hated']: c += f"{label} ODIOU/EVITAR (1-5): {', '.join(data['hated'])}. "
    return c

if 'data_a' in st.session_state:
    context += build_context_string(st.session_state['data_a'], "PERFIL A")
    blocked_ids += st.session_state['data_a']['watched_ids']

if mode == "Casal" and 'data_b' in st.session_state:
    context += build_context_string(st.session_state['data_b'], "PERFIL B")
    blocked_ids += st.session_state['data_b']['watched_ids']

# --- APP PRINCIPAL ---

query = st.text_area("O que você busca?", placeholder="Deixe vazio para recomendação automática baseada nas suas notas...")

btn_label = "🎲 Surpreenda-me" if not query else "🚀 Buscar"

if st.button(btn_label, type="primary"):
    
    # Lógica Automática
    if not query:
        if not context:
            st.error("Para recomendação automática, sincronize seu Trakt primeiro!")
            st.stop()
        final_prompt = f"Analise este perfil de notas: {context}. Recomende algo que ele vai AMAR e evite o que ele ODEIA."
        query_display = "Modo Automático (Baseado em Notas)"
    else:
        final_prompt = f"Pedido: {query}. Contexto: {context}"
        query_display = query

    with st.spinner("Analisando perfil, buscando títulos e verificando streamings..."):
        try:
            # 1. Embedding
            vector = genai.embed_content(model="models/text-embedding-004", content=final_prompt)['embedding']

            # 2. Busca no Supabase
            response = supabase.rpc(db_func, {
                "query_embedding": vector,
                "match_threshold": threshold,
                "match_count": 40,
                "filter_ids": blocked_ids
            }).execute()

            # 3. Filtros
            results = []
            if response.data:
                for item in response.data:
                    if len(results) >= 5: break
                    
                    is_ok, flat, rent = get_watch_providers(item['id'], api_type, my_services if my_services else None)
                    if is_ok:
                        item['providers'] = flat
                        item['rent'] = rent
                        results.append(item)
            
            # 4. Exibição
            if not results:
                st.warning("Nada encontrado com esses critérios/streamings.")
            else:
                st.subheader(f"Resultados para: {query_display}")
                for item in results:
                    c1, c2 = st.columns([1, 3])
                    
                    with c1:
                        img = item['poster_path']
                        if img: st.image(TMDB_IMAGE + img, use_container_width=True)
                        
                        if item['providers']:
                            cols = st.columns(len(item['providers']))
                            for i, p in enumerate(item['providers']):
                                with cols[i]: st.image(TMDB_LOGO + p['logo_path'], width=30)

                    with c2:
                        # Nota TMDB
                        rating = float(item.get('vote_average', 0) or 0)
                        stars = "⭐" * int(round(rating/2)) 
                        st.markdown(f"### {item['title']} | {rating:.1f}/10 {stars}")
                        
                        match_score = int(item['similarity']*100)
                        st.progress(match_score, text=f"Match IA: {match_score}%")
                        
                        expl = explain_choice(item['title'], context if context else "Geral", query_display, item['overview'], rating)
                        st.info(f"💡 {expl}")
                        
                        # Botões
                        col_btn1, col_btn2 = st.columns(2)
                        trailer_url = get_trailer_url(item['id'], api_type)
                        if trailer_url:
                            with col_btn1: st.link_button("▶️ Trailer", trailer_url)
                        
                        trakt_link = get_trakt_url(item['id'], api_type)
                        with col_btn2: st.link_button("📝 Trakt", trakt_link)
                        
                        with st.expander("Sinopse"): st.write(item['overview'])
                    
                    st.divider()

        except Exception as e:
            st.error(f"Erro: {e}")
