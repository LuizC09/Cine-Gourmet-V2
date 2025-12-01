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
    """Pega estatísticas brutas do usuário"""
    headers = {
        'Content-Type': 'application/json',
        'trakt-api-version': '2',
        'trakt-api-key': TRAKT_CLIENT_ID
    }
    try:
        r = requests.get(f"https://api.trakt.tv/users/{username}/stats", headers=headers)
        if r.status_code == 200: return r.json()
    except: return None
    return None

def get_trakt_profile_data(username):
    """Baixa um resumo do gosto do usuário para a IA"""
    headers = {'Content-Type': 'application/json', 'trakt-api-version': '2', 'trakt-api-key': TRAKT_CLIENT_ID}
    
    data = {"history": [], "favorites": [], "watched_ids": []}
    
    try:
        # 1. Watched IDs (Para bloquear repetidos)
        r_watched = requests.get(f"https://api.trakt.tv/users/{username}/watched/movies", headers=headers)
        if r_watched.status_code == 200:
            data["watched_ids"] = [i['movie']['ids']['tmdb'] for i in r_watched.json() if i['movie']['ids'].get('tmdb')]

        # 2. Histórico Recente
        r_hist = requests.get(f"https://api.trakt.tv/users/{username}/history/movies?limit=10", headers=headers)
        if r_hist.status_code == 200:
            data["history"] = [i['movie']['title'] for i in r_hist.json()]

        # 3. Favoritos (Nota 9 ou 10)
        r_fav = requests.get(f"https://api.trakt.tv/users/{username}/ratings/movies/9,10?limit=10", headers=headers)
        if r_fav.status_code == 200:
            data["favorites"] = [i['movie']['title'] for i in r_fav.json()]
            
    except: pass
    return data

def get_watch_providers(movie_id, filter_providers=None):
    """
    Retorna onde assistir. 
    Se filter_providers for passado (lista de nomes), retorna True/False se está disponível neles.
    """
    url = f"https://api.themoviedb.org/3/movie/{movie_id}/watch/providers?api_key={TMDB_API_KEY}"
    try:
        r = requests.get(url)
        data = r.json()
        if 'results' in data and 'BR' in data['results']:
            br = data['results']['BR']
            flatrate = br.get('flatrate', [])
            rent = br.get('rent', [])
            
            # Lógica de Filtro
            if filter_providers:
                # Normaliza nomes (ex: 'Disney Plus' -> 'Disney+')
                avail_names = [p['provider_name'] for p in flatrate]
                # Verifica se ALGUM dos meus streamings tem o filme
                is_available = any(my_prov in avail_names for my_prov in filter_providers)
                return is_available, flatrate, rent
            
            return True, flatrate, rent # Sem filtro, retorna tudo
    except: pass
    return False, [], []

def explain_choice_solo(movie, favorites_list, user_query, overview):
    """Explica a escolha conectando pontos específicos"""
    
    # 1. Limpeza de Dados
    if isinstance(favorites_list, list):
        favs_str = ", ".join(favorites_list[:5])
    else:
        favs_str = str(favorites_list)
        
    if not favs_str: favs_str = "Cinema em geral"

    prompt = f"""
    Atue como um curador de cinema técnico.
    
    DADOS:
    - O usuário ama: {favs_str}.
    - O usuário pediu: "{user_query}".
    - Filme Recomendado: "{movie}".
    - Sinopse: "{overview}".

    TAREFA:
    Escreva uma justificativa de UMA frase explicando por que esse filme atende o pedido.
    REGRA: Não seja genérico. Cite um elemento concreto (a fotografia, o diretor, o plot, a atmosfera) que conecta o filme ao pedido.
    Comece com: "Porque..."
    """
    
    try:
        # MUDANÇA AQUI: Trocamos 'gemini-1.5-flash' por 'gemini-pro' que é universal
        safe = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
        model = genai.GenerativeModel('gemini-pro', safety_settings=safe) 
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        return f"Erro técnico: {str(e)}"

def explain_choice_couple(movie, persona_a, persona_b, overview):
    """Explica a escolha para o CASAL"""
    prompt = f"""
    Contexto: O Usuário A gosta de: {persona_a}.
    O Usuário B gosta de: {persona_b}.
    Filme recomendado: "{movie}" (Sinopse: {overview}).
    
    Explique em UMA frase curta e divertida por que esse filme resolve o problema de escolher algo que os dois gostem.
    """
    try:
        # MUDANÇA AQUI TAMBÉM: 'gemini-pro'
        model = genai.GenerativeModel('gemini-pro')
        return model.generate_content(prompt).text.strip()
    except: return "Um ótimo meio termo para o casal."

# === INTERFACE ===

st.title("🍿 CineGourmet Ultimate")
st.markdown("**IA + Psicologia + Streaming**")

# --- CONFIGURAÇÃO LATERAL ---
with st.sidebar:
    st.header("⚙️ Configuração")
    
    mode = st.radio("Modo de Uso", ["Solo (Só eu)", "Casal (Eu + Mozão)"])
    
    st.divider()
    
    # INPUTS DE USUÁRIO
    user_a = st.text_input("Seu Trakt User", key="user_a")
    user_b = None
    if mode == "Casal (Eu + Mozão)":
        user_b = st.text_input("Trakt User do Parceiro(a)", key="user_b")
    
    if st.button("🔄 Sincronizar Perfis"):
        with st.spinner("Baixando dados do Trakt..."):
            # Perfil A
            if user_a:
                data_a = get_trakt_profile_data(user_a)
                stats_a = get_trakt_stats(user_a)
                st.session_state['data_a'] = data_a
                st.session_state['stats_a'] = stats_a
            
            # Perfil B
            if user_b:
                data_b = get_trakt_profile_data(user_b)
                st.session_state['data_b'] = data_b
            
            st.success("Sincronizado!")

    # EXIBIÇÃO DE STATS (VOLTOU!)
    if 'stats_a' in st.session_state and st.session_state['stats_a']:
        st.divider()
        stats = st.session_state['stats_a']
        minutos = stats['movies']['minutes']
        horas = minutes = int(minutos / 60)
        dias = round(horas / 24, 1)
        
        c1, c2 = st.columns(2)
        c1.metric("Filmes Vistos", stats['movies']['watched'])
        c2.metric("Horas de Vida", horas, help=f"{dias} dias inteiros assistindo filme!")
    
    st.divider()
    
    # FILTRO DE STREAMING
    st.subheader("📺 Meus Streamings")
    st.caption("Se marcar, só recomendo o que tem neles.")
    
    # Lista dos principais no BR (Nomes exatos do TMDB)
    available_services = ["Netflix", "Amazon Prime Video", "Disney Plus", "Max", "Apple TV Plus", "Globoplay"]
    my_services = st.multiselect("O que você assina?", available_services)
    
    threshold = st.slider("Nível de Ousadia", 0.0, 1.0, 0.45)

# --- ÁREA PRINCIPAL ---

# Montagem do Prompt baseada no Modo
prompt_context = ""
blocked_ids = []

if 'data_a' in st.session_state:
    da = st.session_state['data_a']
    summary_a = f"Histórico: {', '.join(da['history'])}. Favoritos: {', '.join(da['favorites'])}."
    prompt_context += f"PERFIL A: {summary_a} "
    blocked_ids += da['watched_ids']

if mode == "Casal (Eu + Mozão)" and 'data_b' in st.session_state:
    db = st.session_state['data_b']
    summary_b = f"Histórico: {', '.join(db['history'])}. Favoritos: {', '.join(db['favorites'])}."
    prompt_context += f"PERFIL B (PARCEIRO): {summary_b}. O OBJETIVO É AGRADAR OS DOIS."
    blocked_ids += db['watched_ids'] # Bloqueia o que QUALQUER UM dos dois já viu

# Input de Busca
user_query = st.text_area("O que vamos assistir?", placeholder="Ex: Suspense curto..." if mode == "Solo" else "Ex: Algo que a gente não brigue pra escolher...")

if st.button("🚀 Recomendar", type="primary"):
    if not user_query:
        st.warning("Diga o que vocês querem!")
    else:
        full_prompt = f"{user_query}. {prompt_context}"
        
        with st.spinner("1. Analisando psicologia... 2. Buscando filmes... 3. Filtrando streamings..."):
            try:
                # 1. Embed
                vector = genai.embed_content(model="models/text-embedding-004", content=full_prompt)['embedding']

                # 2. Busca Ampliada (Pega 40 filmes para ter margem pro filtro)
                response = supabase.rpc("match_movies", {
                    "query_embedding": vector,
                    "match_threshold": threshold,
                    "match_count": 40, # Pega MUITOS para poder filtrar depois
                    "filter_ids": blocked_ids
                }).execute()
                
                final_list = []
                
                # 3. Filtragem Python (Streaming)
                if response.data:
                    for m in response.data:
                        if len(final_list) >= 4: break # Já achamos o top 4
                        
                        # Verifica onde passa
                        is_ok, flatrate, rent = get_watch_providers(m['id'], my_services if my_services else None)
                        
                        if is_ok:
                            m['providers_flat'] = flatrate
                            m['providers_rent'] = rent
                            final_list.append(m)
                
                # 4. Exibição
                if not final_list:
                    st.error("Putz! Encontrei filmes bons, mas NENHUM está nos seus streamings. Tente desmarcar o filtro ou baixar a 'Ousadia'.")
                else:
                    for m in final_list:
                        c1, c2 = st.columns([1, 3])
                        with c1:
                            poster = m['poster_path'] if m['poster_path'] else ""
                            if poster and not poster.startswith("http"): poster = TMDB_IMAGE + poster
                            st.image(poster, use_container_width=True)
                            
                            # Ícones de Streaming
                            if m['providers_flat']:
                                st.caption("Disponível em:")
                                cols = st.columns(len(m['providers_flat']))
                                for i, p in enumerate(m['providers_flat']):
                                    with cols[i]: st.image(TMDB_LOGO + p['logo_path'], width=30)
                            elif m['providers_rent']:
                                st.caption("Aluguel:")
                                st.write(", ".join([p['provider_name'] for p in m['providers_rent']]))

                        with c2:
                            st.subheader(m['title'])
                            match_score = int(m['similarity']*100)
                            st.progress(match_score, text=f"Match: {match_score}%")
                            
                            # Explicação Inteligente (REVISADA)
                            if "Solo" in mode: 
                                # Garante que favorites é uma lista antes de passar
                                raw_favs = st.session_state.get('data_a', {}).get('favorites', [])
                                
                                expl = explain_choice_solo(
                                    m['title'], 
                                    raw_favs, 
                                    user_query, 
                                    m['overview']
                                )
                            else:
                                expl = explain_choice_couple(m['title'], "Perfil A", "Perfil B", m['overview'])
                                
                            st.info(f"💡 {expl}")
                            with st.expander("Sinopse"): st.write(m['overview'])
                        st.divider()

            except Exception as e:
                st.error(f"Erro: {e}")



