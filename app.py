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
    st.error("🚨 Configure os Secrets no Streamlit!")
    st.stop()

genai.configure(api_key=GOOGLE_API_KEY)
st.set_page_config(page_title="CineGourmet Ultimate", page_icon="🍿", layout="wide")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
TMDB_IMAGE = "https://image.tmdb.org/t/p/w500"
TMDB_LOGO = "https://image.tmdb.org/t/p/original"

# === FUNÇÕES ===

def get_trakt_profile_data(username, content_type="movies"):
    """Pega histórico de filmes ou séries"""
    headers = {'Content-Type': 'application/json', 'trakt-api-version': '2', 'trakt-api-key': TRAKT_CLIENT_ID}
    data = {"history": [], "favorites": [], "watched_ids": []}
    
    # Define endpoints baseados no tipo (movies ou shows)
    t_type = "shows" if content_type == "tv" else "movies"
    
    try:
        # Watched
        r_watched = requests.get(f"https://api.trakt.tv/users/{username}/watched/{t_type}", headers=headers)
        if r_watched.status_code == 200:
            # Trakt usa 'show' para séries e 'movie' para filmes no JSON
            key = 'show' if content_type == "tv" else 'movie'
            data["watched_ids"] = [i[key]['ids']['tmdb'] for i in r_watched.json() if i[key]['ids'].get('tmdb')]

        # Favoritos (Rating 9/10)
        r_fav = requests.get(f"https://api.trakt.tv/users/{username}/ratings/{t_type}/9,10?limit=10", headers=headers)
        if r_fav.status_code == 200:
            key = 'show' if content_type == "tv" else 'movie'
            data["favorites"] = [i[key]['title'] for i in r_fav.json()]
    except: pass
    return data

def get_watch_providers(content_id, content_type, filter_providers=None):
    """Busca streaming (Suporta 'movie' ou 'tv')"""
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

def explain_choice(title, persona, user_query, overview):
    prompt = f"""
    Atue como um curador de cinema.
    DADOS: Usuário gosta de: {persona}. Pediu: "{user_query}".
    Recomendação: "{title}" (Sinopse: {overview}).
    TAREFA: Em UMA frase, explique por que essa escolha é perfeita.
    """
    try:
        model = genai.GenerativeModel('models/gemini-2.0-flash')
        return model.generate_content(prompt).text.strip()
    except: return "Uma escolha sólida baseada no seu perfil."

# === INTERFACE ===

st.title("🍿 CineGourmet Ultimate")

# --- SIDEBAR ---
with st.sidebar:
    st.header("⚙️ Configuração")
    
    # 1. ESCOLHA O TIPO (FILME OU SÉRIE)
    content_type = st.radio("O que vamos ver?", ["Filmes 🎬", "Séries 📺"])
    is_tv = "Séries" in content_type
    api_type = "tv" if is_tv else "movie" # Para usar na API do TMDB
    db_func = "match_tv_shows" if is_tv else "match_movies" # Para usar no Supabase
    
    st.divider()
    
    # 2. MODO CASAL/SOLO
    mode = st.radio("Modo", ["Solo", "Casal"])
    
    user_a = st.text_input("Seu Trakt User", key="user_a")
    user_b = None if mode == "Solo" else st.text_input("Parceiro(a)", key="user_b")
    
    if st.button("🔄 Sincronizar"):
        with st.spinner("Lendo mentes..."):
            if user_a: st.session_state['data_a'] = get_trakt_profile_data(user_a, api_type)
            if user_b: st.session_state['data_b'] = get_trakt_profile_data(user_b, api_type)
            st.success("Sincronizado!")
    
    st.divider()
    st.subheader("📺 Meus Streamings")
    services = ["Netflix", "Amazon Prime Video", "Disney Plus", "Max", "Apple TV Plus", "Globoplay"]
    my_services = st.multiselect("O que você assina?", services)
    threshold = st.slider("Ousadia", 0.0, 1.0, 0.45)

# --- APP PRINCIPAL ---

# Monta Contexto
context = ""
blocked_ids = []

if 'data_a' in st.session_state:
    d = st.session_state['data_a']
    if d['favorites']: context += f"PERFIL A ama: {', '.join(d['favorites'])}. "
    blocked_ids += d['watched_ids']

if mode == "Casal" and 'data_b' in st.session_state:
    d = st.session_state['data_b']
    if d['favorites']: context += f"PERFIL B ama: {', '.join(d['favorites'])}. "
    blocked_ids += d['watched_ids']

# Input do Usuário
query = st.text_area("O que você busca?", placeholder="Deixe vazio para o modo 'Surpreenda-me'...")

# Botão principal
btn_label = "🎲 Surpreenda-me" if not query else "🚀 Buscar"
if st.button(btn_label, type="primary"):
    
    # Lógica Automática (Surpreenda-me)
    if not query:
        if not context:
            st.error("Para usar o modo automático, preciso que você Sincronize um perfil Trakt primeiro!")
            st.stop()
        final_prompt = f"Recomende algo incrível baseado APENAS neste perfil: {context}. Ignore clichês."
        query_display = "Baseado no seu gosto (Automático)"
    else:
        final_prompt = f"{query}. {context}"
        query_display = query

    with st.spinner("Consultando o oráculo..."):
        try:
            # 1. Embedding
            vector = genai.embed_content(model="models/text-embedding-004", content=final_prompt)['embedding']

            # 2. Busca no Supabase (Filmes ou Séries)
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
                    
                    # Filtra Streaming
                    is_ok, flat, rent = get_watch_providers(item['id'], api_type, my_services if my_services else None)
                    if is_ok:
                        item['providers'] = flat
                        item['rent'] = rent
                        results.append(item)
            
            # 4. Exibição
            if not results:
                st.warning("Nada encontrado nos seus streamings ou com essa descrição.")
            else:
                st.subheader(f"Resultados para: {query_display}")
                for item in results:
                    c1, c2 = st.columns([1, 3])
                    
                    with c1:
                        img = item['poster_path']
                        if img: st.image(TMDB_IMAGE + img, use_container_width=True)
                        
                        # Ícones de Streaming
                        if item['providers']:
                            cols = st.columns(len(item['providers']))
                            for i, p in enumerate(item['providers']):
                                with cols[i]: st.image(TMDB_LOGO + p['logo_path'], width=30)

                    with c2:
                        # Título e NOTA (Estrelas)
                        rating = item.get('vote_average', 0) or 0
                        stars = "⭐" * int(rating/2) # Converte nota 10 para 5 estrelas
                        st.markdown(f"### {item['title']} | {rating:.1f}/10 {stars}")
                        
                        st.progress(int(item['similarity']*100), text=f"Match IA: {int(item['similarity']*100)}%")
                        
                        expl = explain_choice(item['title'], context if context else "Geral", query_display, item['overview'])
                        st.info(f"💡 {expl}")
                        
                        with st.expander("Sinopse"): st.write(item['overview'])
                    
                    st.divider()

        except Exception as e:
            st.error(f"Erro: {e}")
