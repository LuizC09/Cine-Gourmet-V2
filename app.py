import streamlit as st
import google.generativeai as genai
import requests
from supabase import create_client, Client

# === SEGREDOS (O Cofre Digital) ===
# O código busca as chaves nas configurações do servidor
try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
    TRAKT_CLIENT_ID = st.secrets["TRAKT_CLIENT_ID"]
except FileNotFoundError:
    st.error("As chaves de API não foram encontradas. Configure os 'Secrets' no Streamlit Cloud.")
    st.stop()

TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"

# Configurações
genai.configure(api_key=GOOGLE_API_KEY)
st.set_page_config(page_title="CineGourmet AI", page_icon="🍿", layout="wide")

@st.cache_resource
def init_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = init_supabase()

def get_trakt_history(username):
    headers = {
        'Content-Type': 'application/json',
        'trakt-api-version': '2',
        'trakt-api-key': TRAKT_CLIENT_ID
    }
    # Tenta pegar histórico ou watchlist
    url = f"https://api.trakt.tv/users/{username}/history/movies?limit=5"
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return [item['movie']['title'] for item in response.json()]
    except:
        return None
    return None

def fix_poster(path):
    if not path: return "https://via.placeholder.com/500x750?text=Sem+Poster"
    return f"{TMDB_IMAGE_BASE}{path if path.startswith('/') else '/' + path}"

# === INTERFACE ===
st.title("🍿 CineGourmet: Cloud Edition")
st.caption("Powered by Google Gemini & Supabase")

with st.sidebar:
    st.header("Configurações")
    trakt_user = st.text_input("Usuário Trakt (Opcional)")
    threshold = st.slider("Exatidão", 0.0, 1.0, 0.45)

query = st.text_area("O que vamos assistir hoje?", placeholder="Descreva a vibe, o enredo ou misture filmes...")

if st.button("Recomendar", type="primary"):
    if not query:
        st.warning("Digita algo!")
    else:
        contexto = ""
        if trakt_user:
            with st.spinner("Analisando perfil Trakt..."):
                filmes = get_trakt_history(trakt_user)
                if filmes:
                    st.toast(f"Perfil carregado: {', '.join(filmes)}")
                    contexto = f" O usuário gosta de: {', '.join(filmes)}."
        
        with st.spinner("Consultando o Oráculo..."):
            try:
                # Gera vetor com Google (mesmo modelo do banco)
                vector = genai.embed_content(
                    model="models/text-embedding-004",
                    content=query + contexto,
                    task_type="retrieval_query"
                )['embedding']

                response = supabase.rpc("match_movies", {
                    "query_embedding": vector,
                    "match_threshold": threshold,
                    "match_count": 8
                }).execute()

                if not response.data:
                    st.error("Nenhum filme encontrado. Tente baixar a 'Exatidão'.")
                else:
                    cols = st.columns(4)
                    for i, movie in enumerate(response.data):
                        with cols[i % 4]:
                            st.image(fix_poster(movie['poster_path']), use_container_width=True)
                            st.markdown(f"**{movie['title']}**")
                            with st.expander("Ver Sinopse"):
                                st.write(movie['overview'])
                                st.caption(f"Match: {int(movie['similarity']*100)}%")

            except Exception as e:
                st.error(f"Erro: {e}")