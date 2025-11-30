import streamlit as st
import google.generativeai as genai
import requests
from supabase import create_client

# === CONFIGURAÇÃO ===
try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
    TRAKT_CLIENT_ID = st.secrets["TRAKT_CLIENT_ID"]
except:
    st.error("Configure os Secrets!")
    st.stop()

genai.configure(api_key=GOOGLE_API_KEY)
st.set_page_config(page_title="CineGourmet Pro", page_icon="🧠", layout="wide")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
TMDB_IMAGE = "https://image.tmdb.org/t/p/w500"

# === FUNÇÕES INTELIGENTES ===

def get_trakt_watched_ids(username):
    """Pega os IDs (TMDB) de TUDO que o usuário já viu para não repetir"""
    headers = {
        'Content-Type': 'application/json',
        'trakt-api-version': '2',
        'trakt-api-key': TRAKT_CLIENT_ID
    }
    # Pega o histórico de assistidos (apenas IDs para ser leve)
    url = f"https://api.trakt.tv/users/{username}/watched/movies"
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            # Cria uma lista só com os números dos IDs do TMDB
            watched_ids = [item['movie']['ids']['tmdb'] for item in data if item['movie']['ids'].get('tmdb')]
            return watched_ids
    except:
        return []
    return []

def get_trakt_profile_text(username):
    """Cria o texto para a IA ler o perfil"""
    headers = {
        'Content-Type': 'application/json',
        'trakt-api-version': '2',
        'trakt-api-key': TRAKT_CLIENT_ID
    }
    url_hist = f"https://api.trakt.tv/users/{username}/history/movies?limit=10"
    url_favs = f"https://api.trakt.tv/users/{username}/ratings/movies/9,10?limit=10"
    
    texto_perfil = ""
    try:
        # Últimos vistos
        r = requests.get(url_hist, headers=headers)
        if r.status_code == 200:
            filmes = [i['movie']['title'] for i in r.json()]
            texto_perfil += f"Recentemente assistiu: {', '.join(filmes)}. "
            
        # Favoritos
        r = requests.get(url_favs, headers=headers)
        if r.status_code == 200:
            filmes = [i['movie']['title'] for i in r.json()]
            texto_perfil += f"Seus favoritos supremos são: {', '.join(filmes)}."
    except: pass
    
    return texto_perfil

def analyze_taste(profile_text):
    if not profile_text: return ""
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"""
    Analise este histórico de filmes: "{profile_text}"
    Descreva em UMA frase o gosto dessa pessoa (ex: Gosta de terror psicológico e dramas lentos).
    """
    try:
        return model.generate_content(prompt).text.strip()
    except: return ""

# === INTERFACE ===
st.title("🧠 CineGourmet: Anti-Déjà Vu Edition")
st.caption("Agora filtra automaticamente o que você já assistiu no Trakt.")

with st.sidebar:
    st.header("Conectar Trakt")
    trakt_user = st.text_input("Usuário Trakt")
    
    # Variáveis de Sessão para guardar o estado
    if 'watched_ids' not in st.session_state: st.session_state['watched_ids'] = []
    if 'persona' not in st.session_state: st.session_state['persona'] = ""

    if trakt_user and st.button("Carregar Perfil"):
        with st.spinner("Baixando histórico completo..."):
            # 1. Pega IDs para bloquear
            ids = get_trakt_watched_ids(trakt_user)
            st.session_state['watched_ids'] = ids
            
            # 2. Pega texto para analisar
            raw_text = get_trakt_profile_text(trakt_user)
            persona = analyze_taste(raw_text)
            st.session_state['persona'] = persona
            
            st.success(f"Perfil carregado! {len(ids)} filmes bloqueados.")

    if st.session_state['persona']:
        st.info(f"🧬 **Perfil:** {st.session_state['persona']}")

st.divider()

user_query = st.text_area("O que você quer ver hoje?", placeholder="Ex: Sci-fi cabeça...")

if st.button("Recomendar", type="primary"):
    if not user_query:
        st.warning("Digita algo!")
    else:
        # Monta Prompt
        prompt_final = user_query
        if st.session_state['persona']:
            prompt_final += f". O usuário tem esse gosto: {st.session_state['persona']}"

        with st.spinner("Processando..."):
            try:
                # 1. Vetor
                vector = genai.embed_content(
                    model="models/text-embedding-004",
                    content=prompt_final,
                    task_type="retrieval_query"
                )['embedding']

                # 2. Busca no Supabase COM FILTRO (A mágica)
                response = supabase.rpc("match_movies", {
                    "query_embedding": vector,
                    "match_threshold": 0.40,
                    "match_count": 8,
                    "filter_ids": st.session_state['watched_ids'] # <--- AQUI BLOQUEIA OS VISTOS
                }).execute()

                if response.data:
                    cols = st.columns(4)
                    for i, m in enumerate(response.data):
                        with cols[i % 4]:
                            poster = m['poster_path'] if m['poster_path'] else ""
                            if poster and not poster.startswith("http"):
                                poster = TMDB_IMAGE + (poster if poster.startswith("/") else "/" + poster)
                            
                            st.image(poster, use_container_width=True)
                            st.markdown(f"**{m['title']}**")
                            with st.expander("Info"):
                                st.write(m['overview'])
                                st.caption(f"Match: {int(m['similarity']*100)}%")
                else:
                    st.warning("Nada encontrado. Talvez você já tenha visto todos os filmes bons desse gênero! 😂")
            
            except Exception as e:
                st.error(f"Erro: {e}")
