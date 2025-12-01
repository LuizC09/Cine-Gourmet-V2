import streamlit as st
import google.generativeai as genai
import requests
import pandas as pd
import plotly.express as px
from supabase import create_client

# === CONFIGURAÇÃO ===
try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
    TRAKT_CLIENT_ID = st.secrets["TRAKT_CLIENT_ID"]
    TMDB_API_KEY = st.secrets["TMDB_API_KEY"] # <--- VOCÊ PRECISA ADICIONAR ISSO NOS SECRETS!
except:
    st.error("Configure os Secrets! Falta o TMDB_API_KEY.")
    st.stop()

genai.configure(api_key=GOOGLE_API_KEY)
st.set_page_config(page_title="CineGourmet Pro", page_icon="🍿", layout="wide")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
TMDB_IMAGE = "https://image.tmdb.org/t/p/w500"
TMDB_LOGO = "https://image.tmdb.org/t/p/original"

# === FUNÇÕES NOVAS (STREAMING) ===

def get_watch_providers(movie_id):
    """Descobre onde o filme está passando no Brasil"""
    url = f"https://api.themoviedb.org/3/movie/{movie_id}/watch/providers?api_key={TMDB_API_KEY}"
    try:
        r = requests.get(url)
        data = r.json()
        if 'results' in data and 'BR' in data['results']:
            br_providers = data['results']['BR']
            # Prioridade: Flatrate (Assinatura) > Rent (Aluguel)
            if 'flatrate' in br_providers:
                return br_providers['flatrate'][:3] # Pega os top 3 streamings
            elif 'rent' in br_providers:
                return br_providers['rent'][:3] # Se não tiver streaming, mostra onde alugar
    except: pass
    return []

# ... (MANTENHA AS OUTRAS FUNÇÕES: get_trakt_stats, get_trakt_watched_ids, etc.) ...
# Vou resumir aqui para não ficar gigante, mas você mantém as funções antigas de Trakt e Explain

def get_trakt_watched_ids(username):
    # ... (Copie do código anterior) ...
    headers = {'Content-Type': 'application/json', 'trakt-api-version': '2', 'trakt-api-key': TRAKT_CLIENT_ID}
    url = f"https://api.trakt.tv/users/{username}/watched/movies"
    try:
        r = requests.get(url, headers=headers)
        if r.status_code == 200:
            return [i['movie']['ids']['tmdb'] for i in r.json() if i['movie']['ids'].get('tmdb')]
    except: return []
    return []

def get_trakt_profile_text(username):
    # ... (Copie do código anterior) ...
    headers = {'Content-Type': 'application/json', 'trakt-api-version': '2', 'trakt-api-key': TRAKT_CLIENT_ID}
    url_favs = f"https://api.trakt.tv/users/{username}/ratings/movies/9,10?limit=5"
    try:
        r = requests.get(url_favs, headers=headers)
        if r.status_code == 200:
            filmes = [i['movie']['title'] for i in r.json()]
            return f"Favoritos: {', '.join(filmes)}"
    except: pass
    return ""

def explain_choice(movie_title, user_persona, movie_overview):
    # ... (Copie do código anterior) ...
    prompt = f"O usuário gosta de: {user_persona}. Recomendei '{movie_title}'. Explique em 1 frase curta por que combina."
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        return model.generate_content(prompt).text.strip()
    except: return "Match de vibe."

# === INTERFACE ===

col_logo, col_title = st.columns([1, 5])
with col_logo: st.write("🍿")
with col_title:
    st.title("CineGourmet AI")
    st.caption("Com onde assistir (Brasil)")

with st.sidebar:
    st.header("👤 Perfil")
    trakt_user = st.text_input("Usuário Trakt")
    
    if 'watched_ids' not in st.session_state: st.session_state['watched_ids'] = []
    if 'persona' not in st.session_state: st.session_state['persona'] = ""

    if trakt_user and st.button("Sincronizar"):
        with st.spinner("Analisando..."):
            st.session_state['watched_ids'] = get_trakt_watched_ids(trakt_user)
            st.session_state['persona'] = get_trakt_profile_text(trakt_user)
            st.success("Sincronizado!")
            
    st.divider()
    threshold = st.slider("Ousadia", 0.1, 1.0, 0.45)

user_query = st.text_area("O que vamos ver?", placeholder="Ex: Suspense policial...")

if st.button("🎬 Buscar", type="primary"):
    if not user_query:
        st.warning("Digita algo!")
    else:
        prompt_final = user_query
        if st.session_state['persona']:
            prompt_final += f". Gosto do usuário: {st.session_state['persona']}"

        with st.spinner("Buscando filmes e verificando disponibilidade..."):
            try:
                # 1. Embed
                vector = genai.embed_content(model="models/text-embedding-004", content=prompt_final)['embedding']

                # 2. Search
                response = supabase.rpc("match_movies", {
                    "query_embedding": vector, 
                    "match_threshold": threshold, 
                    "match_count": 4,
                    "filter_ids": st.session_state['watched_ids']
                }).execute()

                if response.data:
                    for m in response.data:
                        c1, c2 = st.columns([1, 3])
                        
                        with c1:
                            poster = m['poster_path'] if m['poster_path'] else ""
                            if poster and not poster.startswith("http"):
                                poster = TMDB_IMAGE + (poster if poster.startswith("/") else "/" + poster)
                            st.image(poster, use_container_width=True)
                            
                            # === EXIBIR STREAMINGS ===
                            providers = get_watch_providers(m['id'])
                            if providers:
                                st.caption("Assista em:")
                                # Mostra os ícones lado a lado
                                cols_prov = st.columns(len(providers))
                                for idx, prov in enumerate(providers):
                                    with cols_prov[idx]:
                                        logo_url = TMDB_LOGO + prov['logo_path']
                                        st.image(logo_url, width=30)
                            else:
                                st.caption("Indisponível em streamings BR")

                        with c2:
                            st.markdown(f"### {m['title']}")
                            match_score = int(m['similarity']*100)
                            st.metric("Match", f"{match_score}%")
                            
                            explanation = explain_choice(m['title'], st.session_state['persona'], m['overview'])
                            st.info(f"🤖 {explanation}")
                            
                            with st.expander("Sinopse"):
                                st.write(m['overview'])
                        st.divider()
                else:
                    st.warning("Nada encontrado.")
            except Exception as e:
                st.error(f"Erro: {e}")
