import streamlit as st
import google.generativeai as genai
import requests
import pandas as pd
import plotly.express as px
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

# === FUNÇÕES DE DADOS (TRAKT & TMDB) ===

def get_trakt_stats(username):
    headers = {'Content-Type': 'application/json', 'trakt-api-version': '2', 'trakt-api-key': TRAKT_CLIENT_ID}
    try:
        r = requests.get(f"https://api.trakt.tv/users/{username}/stats", headers=headers)
        if r.status_code == 200: return r.json()
    except: return None
    return None

def get_trakt_profile_data(username):
    headers = {'Content-Type': 'application/json', 'trakt-api-version': '2', 'trakt-api-key': TRAKT_CLIENT_ID}
    data = {"history": [], "favorites": [], "watched_ids": []}
    try:
        r_watched = requests.get(f"https://api.trakt.tv/users/{username}/watched/movies", headers=headers)
        if r_watched.status_code == 200:
            data["watched_ids"] = [i['movie']['ids']['tmdb'] for i in r_watched.json() if i['movie']['ids'].get('tmdb')]
        r_hist = requests.get(f"https://api.trakt.tv/users/{username}/history/movies?limit=10", headers=headers)
        if r_hist.status_code == 200:
            data["history"] = [i['movie']['title'] for i in r_hist.json()]
        r_fav = requests.get(f"https://api.trakt.tv/users/{username}/ratings/movies/9,10?limit=10", headers=headers)
        if r_fav.status_code == 200:
            data["favorites"] = [i['movie']['title'] for i in r_fav.json()]
    except: pass
    return data

def get_watch_providers(movie_id, filter_providers=None):
    url = f"https://api.themoviedb.org/3/movie/{movie_id}/watch/providers?api_key={TMDB_API_KEY}"
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

def explain_choice_solo(movie, favorites_list, user_query, overview):
    # Tratamento para garantir string
    if isinstance(favorites_list, list): favs_str = ", ".join(favorites_list[:5])
    else: favs_str = str(favorites_list)
    if not favs_str: favs_str = "Cinema em geral"

    prompt = f"""
    Atue como um curador de cinema técnico e perspicaz.
    DADOS: Usuário ama: {favs_str}. Pediu: "{user_query}". Recomendação: "{movie}". Sinopse: "{overview}".
    TAREFA: Escreva uma frase explicando por que esse filme atende o pedido. Cite um elemento concreto (direção, roteiro, clima).
    Comece com: "Porque..."
    """
    try:
        # USA O MODELO 2.0 FLASH DA SUA LISTA
        model = genai.GenerativeModel('models/gemini-2.0-flash')
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        return f"Erro na explicação: {str(e)}"

def explain_choice_couple(movie, persona_a, persona_b, overview):
    prompt = f"""
    Contexto: A gosta de: {persona_a}. B gosta de: {persona_b}. Filme: "{movie}" ({overview}).
    Explique em uma frase por que esse filme agrada os dois.
    """
    try:
        # USA O MODELO 2.0 FLASH DA SUA LISTA
        model = genai.GenerativeModel('models/gemini-2.0-flash')
        return model.generate_content(prompt).text.strip()
    except: return "Um ótimo meio termo."

# === INTERFACE ===

st.title("🍿 CineGourmet Ultimate")
st.caption("Powered by Gemini 2.0 Flash")

with st.sidebar:
    st.header("⚙️ Configuração")
    
    # Texto exato para o IF funcionar
    mode = st.radio("Modo de Uso", ["Solo (Só eu)", "Casal (Eu + Mozão)"])
    st.divider()
    
    user_a = st.text_input("Seu Trakt User", key="user_a")
    user_b = None
    if mode == "Casal (Eu + Mozão)":
        user_b = st.text_input("Trakt User do Parceiro(a)", key="user_b")
    
    if st.button("🔄 Sincronizar Perfis"):
        with st.spinner("Baixando dados do Trakt..."):
            if user_a:
                st.session_state['data_a'] = get_trakt_profile_data(user_a)
                st.session_state['stats_a'] = get_trakt_stats(user_a)
            if user_b:
                st.session_state['data_b'] = get_trakt_profile_data(user_b)
            st.success("Sincronizado!")

    if 'stats_a' in st.session_state and st.session_state['stats_a']:
        st.divider()
        stats = st.session_state['stats_a']
        minutos = stats['movies']['minutes']
        c1, c2 = st.columns(2)
        c1.metric("Filmes", stats['movies']['watched'])
        c2.metric("Horas", int(minutos / 60))
    
    st.divider()
    st.subheader("📺 Meus Streamings")
    available_services = ["Netflix", "Amazon Prime Video", "Disney Plus", "Max", "Apple TV Plus", "Globoplay"]
    my_services = st.multiselect("O que você assina?", available_services)
    threshold = st.slider("Nível de Ousadia", 0.0, 1.0, 0.45)

# --- ÁREA PRINCIPAL ---
prompt_context = ""
blocked_ids = []

if 'data_a' in st.session_state:
    da = st.session_state['data_a']
    prompt_context += f"PERFIL A: {', '.join(da['favorites'])}. "
    blocked_ids += da['watched_ids']

if mode == "Casal (Eu + Mozão)" and 'data_b' in st.session_state:
    db = st.session_state['data_b']
    prompt_context += f"PERFIL B: {', '.join(db['favorites'])}. "
    blocked_ids += db['watched_ids']

user_query = st.text_area("O que vamos assistir?", placeholder="Ex: Suspense curto...")

if st.button("🚀 Recomendar", type="primary"):
    if not user_query:
        st.warning("Diga o que vocês querem!")
    else:
        full_prompt = f"{user_query}. {prompt_context}"
        with st.spinner("Processando..."):
            try:
                # Usa embedding 004 (Geralmente disponível sempre)
                vector = genai.embed_content(model="models/text-embedding-004", content=full_prompt)['embedding']

                response = supabase.rpc("match_movies", {
                    "query_embedding": vector,
                    "match_threshold": threshold,
                    "match_count": 40,
                    "filter_ids": blocked_ids
                }).execute()
                
                final_list = []
                if response.data:
                    for m in response.data:
                        if len(final_list) >= 4: break
                        is_ok, flatrate, rent = get_watch_providers(m['id'], my_services if my_services else None)
                        if is_ok:
                            m['providers_flat'] = flatrate
                            m['providers_rent'] = rent
                            final_list.append(m)
                
                if not final_list:
                    st.error("Nenhum filme encontrado nos seus streamings.")
                else:
                    for m in final_list:
                        c1, c2 = st.columns([1, 3])
                        with c1:
                            poster = m['poster_path'] if m['poster_path'] else ""
                            if poster and not poster.startswith("http"): poster = TMDB_IMAGE + poster
                            st.image(poster, use_container_width=True)
                            if m['providers_flat']:
                                cols = st.columns(len(m['providers_flat']))
                                for i, p in enumerate(m['providers_flat']):
                                    with cols[i]: st.image(TMDB_LOGO + p['logo_path'], width=30)
                        with c2:
                            st.subheader(m['title'])
                            st.progress(int(m['similarity']*100), text=f"Match: {int(m['similarity']*100)}%")
                            
                            # Lógica Solo/Casal CORRIGIDA
                            if "Solo" in mode:
                                raw_favs = st.session_state.get('data_a', {}).get('favorites', [])
                                expl = explain_choice_solo(m['title'], raw_favs, user_query, m['overview'])
                            else:
                                expl = explain_choice_couple(m['title'], "Perfil A", "Perfil B", m['overview'])
                                
                            st.info(f"💡 {expl}")
                            with st.expander("Sinopse"): st.write(m['overview'])
                        st.divider()
            except Exception as e:
                st.error(f"Erro Geral: {e}")
