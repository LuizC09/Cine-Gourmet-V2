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

# === FUNÇÕES INTELIGENTES ===

def get_trakt_profile_data(username, content_type="movies"):
    """
    Baixa avaliações e separa o que o usuário AMOU do que ele ODIOU.
    Isso cria um perfil muito mais preciso.
    """
    headers = {'Content-Type': 'application/json', 'trakt-api-version': '2', 'trakt-api-key': TRAKT_CLIENT_ID}
    data = {"history": [], "loved": [], "liked": [], "hated": [], "watched_ids": []}
    
    t_type = "shows" if content_type == "tv" else "movies"
    item_key = 'show' if content_type == "tv" else 'movie'

    try:
        # 1. Watched IDs (Para não repetir)
        r_watched = requests.get(f"https://api.trakt.tv/users/{username}/watched/{t_type}", headers=headers)
        if r_watched.status_code == 200:
            data["watched_ids"] = [i[item_key]['ids']['tmdb'] for i in r_watched.json() if i[item_key]['ids'].get('tmdb')]

        # 2. Histórico (Últimos vistos)
        r_hist = requests.get(f"https://api.trakt.tv/users/{username}/history/{t_type}?limit=10", headers=headers)
        if r_hist.status_code == 200:
            data["history"] = [i[item_key]['title'] for i in r_hist.json()]

        # 3. AVALIAÇÕES (O Pulo do Gato: Ponderar Nota)
        # Baixa as últimas 50 avaliações
        r_ratings = requests.get(f"https://api.trakt.tv/users/{username}/ratings/{t_type}?limit=50", headers=headers)
        if r_ratings.status_code == 200:
            for item in r_ratings.json():
                title = item[item_key]['title']
                rating = item['rating']
                
                if rating >= 9:
                    data["loved"].append(f"{title} ({rating}/10)")
                elif rating >= 7:
                    data["liked"].append(f"{title} ({rating}/10)")
                elif rating <= 5:
                    data["hated"].append(f"{title} ({rating}/10)")

    except Exception as e:
        print(f"Erro Trakt: {e}")
        pass
        
    return data

def get_watch_providers(content_id, content_type, filter_providers=None):
    """Busca onde assistir (Netflix, Prime, etc)"""
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

def explain_choice(title, context_str, user_query, overview, rating):
    prompt = f"""
    Atue como um crítico de cinema perspicaz.
    
    PERFIL DO USUÁRIO:
    {context_str}
    
    PEDIDO: "{user_query}"
    RECOMENDAÇÃO: "{title}" (Nota Pública: {rating}/10).
    SINOPSE: {overview}

    TAREFA:
    Explique em UMA frase por que essa obra se encaixa no perfil do usuário.
    Se o usuário tem filmes que odeia no perfil, mencione sutilmente que este filme é melhor que eles.
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
    
    # Tipo de Conteúdo
    content_type = st.radio("O que vamos ver?", ["Filmes 🎬", "Séries 📺"])
    is_tv = "Séries" in content_type
    api_type = "tv" if is_tv else "movie"
    db_func = "match_tv_shows" if is_tv else "match_movies"
    
    st.divider()
    
    # Modo
    mode = st.radio("Modo", ["Solo", "Casal"])
    
    user_a = st.text_input("Seu Trakt User", key="user_a")
    user_b = None if mode == "Solo" else st.text_input("Parceiro(a)", key="user_b")
    
    if st.button("🔄 Sincronizar (Ler Notas)"):
        with st.spinner("Analisando suas notas no Trakt..."):
            if user_a: st.session_state['data_a'] = get_trakt_profile_data(user_a, api_type)
            if user_b: st.session_state['data_b'] = get_trakt_profile_data(user_b, api_type)
            st.success("Perfil atualizado com suas notas!")
    
    st.divider()
    st.subheader("📺 Meus Streamings")
    services = ["Netflix", "Amazon Prime Video", "Disney Plus", "Max", "Apple TV Plus", "Globoplay"]
    my_services = st.multiselect("O que você assina?", services)
    threshold = st.slider("Ousadia", 0.0, 1.0, 0.45)

# --- CONSTRUÇÃO DO CONTEXTO INTELIGENTE ---
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

# Botão principal
btn_label = "🎲 Surpreenda-me" if not query else "🚀 Buscar"

if st.button(btn_label, type="primary"):
    
    # Lógica Automática vs Manual
    if not query:
        if not context:
            st.error("Para recomendação automática, sincronize seu Trakt primeiro!")
            st.stop()
        final_prompt = f"Analise profundamente este perfil de notas: {context}. Recomende algo que se encaixe nos gostos (AMOU) e fique longe do que ele ODIOU."
        query_display = "Baseado nas suas Notas (Automático)"
    else:
        final_prompt = f"Pedido: {query}. Contexto de Gosto: {context}"
        query_display = query

    with st.spinner("Processando suas notas e preferências..."):
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

            # 3. Filtros (Streaming)
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
                        # Título e NOTA (Estrelas baseadas no TMDB)
                        rating = float(item.get('vote_average', 0) or 0)
                        stars = "⭐" * int(round(rating/2)) 
                        st.markdown(f"### {item['title']} | {rating:.1f}/10 {stars}")
                        
                        match_score = int(item['similarity']*100)
                        st.progress(match_score, text=f"Match IA: {match_score}%")
                        
                        # Explicação com a Nota
                        expl = explain_choice(item['title'], context if context else "Geral", query_display, item['overview'], rating)
                        st.info(f"💡 {expl}")
                        
                        with st.expander("Sinopse"): st.write(item['overview'])
                    
                    st.divider()

        except Exception as e:
            st.error(f"Erro: {e}")
