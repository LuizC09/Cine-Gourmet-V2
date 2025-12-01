import streamlit as st
import google.generativeai as genai
import requests
import pandas as pd
import plotly.express as px  # <--- Nova biblioteca visual (adicione no requirements.txt)
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
st.set_page_config(page_title="CineGourmet Premium", page_icon="🍿", layout="wide")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
TMDB_IMAGE = "https://image.tmdb.org/t/p/w500"

# === FUNÇÕES DE INTELIGÊNCIA E DADOS ===

def get_trakt_stats(username):
    """Pega estatísticas de gênero do Trakt para o gráfico"""
    headers = {
        'Content-Type': 'application/json',
        'trakt-api-version': '2',
        'trakt-api-key': TRAKT_CLIENT_ID
    }
    # O Trakt tem um endpoint pronto de stats!
    url = f"https://api.trakt.tv/users/{username}/stats"
    try:
        r = requests.get(url, headers=headers)
        if r.status_code == 200:
            return r.json()
    except: return None
    return None

def get_trakt_watched_ids(username):
    headers = {'Content-Type': 'application/json', 'trakt-api-version': '2', 'trakt-api-key': TRAKT_CLIENT_ID}
    url = f"https://api.trakt.tv/users/{username}/watched/movies"
    try:
        r = requests.get(url, headers=headers)
        if r.status_code == 200:
            return [i['movie']['ids']['tmdb'] for i in r.json() if i['movie']['ids'].get('tmdb')]
    except: return []
    return []

def get_trakt_profile_text(username):
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
    """Pede pro Gemini explicar a conexão"""
    prompt = f"""
    O usuário gosta de: "{user_persona}".
    Eu recomendei o filme "{movie_title}" (Sinopse: {movie_overview}).
    Explique EM UMA FRASE CURTA E PERSUASIVA por que esse filme combina com o gosto dele.
    Comece com "Porque..."
    """
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        return model.generate_content(prompt).text.strip()
    except: return "Porque combina com a vibe que você pediu."

# === INTERFACE ===

# Cabeçalho Moderno
col_logo, col_title = st.columns([1, 5])
with col_logo:
    st.write("🍿")
with col_title:
    st.title("CineGourmet AI")
    st.caption("Curadoria Psicológica de Cinema")

with st.sidebar:
    st.header("👤 Perfil & DNA")
    trakt_user = st.text_input("Usuário Trakt")
    
    if 'watched_ids' not in st.session_state: st.session_state['watched_ids'] = []
    if 'persona' not in st.session_state: st.session_state['persona'] = ""
    if 'stats' not in st.session_state: st.session_state['stats'] = None

    if trakt_user and st.button("Sincronizar Trakt"):
        with st.spinner("Analisando DNA..."):
            st.session_state['watched_ids'] = get_trakt_watched_ids(trakt_user)
            raw_text = get_trakt_profile_text(trakt_user)
            st.session_state['persona'] = raw_text
            st.session_state['stats'] = get_trakt_stats(trakt_user)
            st.success("Sincronizado!")

    # GRÁFICO DE PIZZA (GENÊROS)
    if st.session_state['stats']:
        st.divider()
        st.subheader("Seu DNA Cinéfilo")
        # Criando dados fictícios baseados nos stats reais (Genres distribution não vem fácil no free, vamos usar distribution de notas ou play count)
        # Para simplificar, vou mostrar distribuição de Play Count por enquanto
        stats = st.session_state['stats']
        if 'genres' in stats: # Se o endpoint retornar generos (as vezes varia)
             pass 
        else:
             # Mostra gráfico de quanto tempo perdeu vendo filmes
             st.metric("Filmes Vistos", stats['movies']['watched'])
             st.metric("Minutos Assistidos", stats['movies']['minutes'])
             
    st.divider()
    threshold = st.slider("Nível de Ousadia", 0.1, 1.0, 0.45, help="Quanto menor, mais óbvio. Quanto maior, mais aleatório.")

# Área Principal
user_query = st.text_area("Descreva o filme perfeito para hoje...", height=100, placeholder="Ex: Um thriller psicológico que se passe em um lugar isolado, tipo O Iluminado, mas moderno.")

if st.button("🎬 Gerar Curadoria", type="primary"):
    if not user_query:
        st.warning("Me dê uma dica do que você quer!")
    else:
        # Prompt
        prompt_final = user_query
        if st.session_state['persona']:
            prompt_final += f". O usuário ama: {st.session_state['persona']}"

        with st.spinner("A IA está assistindo 2.000 filmes simultaneamente..."):
            try:
                # 1. Embed
                vector = genai.embed_content(
                    model="models/text-embedding-004",
                    content=prompt_final,
                    task_type="retrieval_query"
                )['embedding']

                # 2. Search
                response = supabase.rpc("match_movies", {
                    "query_embedding": vector,
                    "match_threshold": threshold,
                    "match_count": 4, # Traz menos filmes, mas com mais qualidade visual
                    "filter_ids": st.session_state['watched_ids']
                }).execute()

                if response.data:
                    st.divider()
                    st.subheader("Recomendações Personalizadas")
                    
                    for m in response.data:
                        # Layout em colunas para cada filme (Poster Esquerda | Info Direita)
                        c1, c2 = st.columns([1, 3])
                        
                        with c1:
                            poster = m['poster_path'] if m['poster_path'] else ""
                            if poster and not poster.startswith("http"):
                                poster = TMDB_IMAGE + (poster if poster.startswith("/") else "/" + poster)
                            st.image(poster, use_container_width=True)
                        
                        with c2:
                            st.markdown(f"### {m['title']}")
                            
                            # NOTA E MATCH
                            col_metrics1, col_metrics2 = st.columns(2)
                            match_score = int(m['similarity']*100)
                            
                            # Simulação de nota (O TMDB vote_average geralmente vai de 0 a 10)
                            # Se você não salvou vote_average no banco, vamos usar popularity como proxy ou fake por enqnt
                            # Dica: Adicione vote_average no etl_worker na proxima vez!
                            col_metrics1.metric("Match IA", f"{match_score}%")
                            
                            # EXPLICAÇÃO PERSONALIZADA (On The Fly)
                            explanation = explain_choice(m['title'], st.session_state['persona'], m['overview'])
                            st.info(f"🤖 **Por que você vai gostar:** {explanation}")
                            
                            with st.expander("Sinopse Oficial"):
                                st.write(m['overview'])
                        
                        st.divider() # Linha separadora entre filmes

                else:
                    st.warning("Nada encontrado. Tente ser menos exigente na 'Ousadia'.")

            except Exception as e:
                st.error(f"Erro: {e}")
