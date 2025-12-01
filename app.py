import streamlit as st
import google.generativeai as genai
import requests
import pandas as pd
from supabase import create_client
from datetime import datetime
import concurrent.futures
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# === 1. CONFIGURAÇÃO E SEGREDOS ===
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
st.set_page_config(page_title="CineGourmet Híbrido", page_icon="🧠", layout="wide")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
TMDB_IMAGE = "https://image.tmdb.org/t/p/w500"
TMDB_LOGO = "https://image.tmdb.org/t/p/original"

# === 2. SESSÃO E CACHE ===

def get_session():
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    return session

session = get_session()

@st.cache_data(ttl=3600)
def get_trakt_profile_data(username, content_type="movies"):
    headers = {'Content-Type': 'application/json', 'trakt-api-version': '2', 'trakt-api-key': TRAKT_CLIENT_ID}
    data = {"history": [], "loved": [], "liked": [], "hated": [], "watched_ids": []}
    t_type = "shows" if content_type == "tv" else "movies"
    item_key = 'show' if content_type == "tv" else 'movie'

    try:
        # Pega IDs Vistos (Aumentei o limite para garantir que pegue tudo recente)
        r_watched = session.get(f"https://api.trakt.tv/users/{username}/watched/{t_type}?limit=1000", headers=headers)
        if r_watched.status_code == 200:
            data["watched_ids"] = [i[item_key]['ids']['tmdb'] for i in r_watched.json() if i[item_key]['ids'].get('tmdb')]

        # Pega Avaliações (Aumentei limite para 100 para ter mais base)
        r_ratings = session.get(f"https://api.trakt.tv/users/{username}/ratings/{t_type}?limit=100", headers=headers)
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

@st.cache_data(ttl=86400)
def get_watch_providers(content_id, content_type):
    url = f"https://api.themoviedb.org/3/{content_type}/{content_id}/watch/providers?api_key={TMDB_API_KEY}"
    try:
        r = session.get(url, timeout=5)
        data = r.json()
        if 'results' in data and 'BR' in data['results']:
            br = data['results']['BR']
            return True, br.get('flatrate', []), br.get('rent', [])
    except: pass
    return False, [], []

@st.cache_data(ttl=86400)
def get_trailer_url(content_id, content_type):
    url = f"https://api.themoviedb.org/3/{content_type}/{content_id}/videos?api_key={TMDB_API_KEY}&language=pt-BR"
    try:
        r = session.get(url, timeout=5)
        data = r.json()
        if 'results' in data:
            for v in data['results']:
                if v['site'] == 'YouTube' and v['type'] == 'Trailer': return f"https://www.youtube.com/watch?v={v['key']}"
            url_en = f"https://api.themoviedb.org/3/{content_type}/{content_id}/videos?api_key={TMDB_API_KEY}&language=en-US"
            r_en = session.get(url_en)
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
    if data.get('hated'): c += f"ODIOU/EVITAR (1-5): {', '.join(data['hated'][:15])}. "
    return c

def explain_choice(title, context_str, user_query, overview, rating):
    prompt = f"""
    Atue como crítico de cinema.
    PERFIL: {context_str}
    PEDIDO: "{user_query}"
    OBRA: "{title}" ({rating}/10).
    SINOPSE: {overview}
    TAREFA: Frase única e persuasiva conectando a obra ao perfil.
    """
    try:
        model = genai.GenerativeModel('models/gemini-2.0-flash')
        return model.generate_content(prompt).text.strip()
    except: return "Recomendação baseada no seu perfil."

# === 3. LÓGICA HÍBRIDA & PARALELA ===

def calculate_hybrid_score(item):
    sim_score = float(item.get('similarity', 0))
    vote = float(item.get('vote_average', 0) or 0)
    rating_score = vote / 10.0
    pop = float(item.get('popularity', 0) or 0)
    pop_score = min(pop / 1000.0, 1.0)
    return (sim_score * 0.7) + (rating_score * 0.2) + (pop_score * 0.1)

def process_single_item(item, api_type, my_services):
    is_ok, flat, rent = get_watch_providers(item['id'], api_type)
    
    has_service = False
    if my_services:
        avail_names = [p['provider_name'] for p in flat]
        has_service = any(s in avail_names for s in my_services)
        if not has_service and not rent: return None
    else:
        has_service = True
    
    if has_service or rent:
        item['providers_flat'] = flat
        item['providers_rent'] = rent
        item['trailer'] = get_trailer_url(item['id'], api_type)
        item['trakt_url'] = get_trakt_url(item['id'], api_type)
        item['hybrid_score'] = calculate_hybrid_score(item)
        return item
    return None

def process_batch_parallel(items, api_type, my_services, limit=5):
    results = []
    # Usa max_workers=5 para evitar sobrecarga no free tier
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(process_single_item, item, api_type, my_services) for item in items]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res:
                results.append(res)
    results.sort(key=lambda x: x['hybrid_score'], reverse=True)
    return results[:limit]

# === 4. PERSISTÊNCIA ===

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

def save_block(username, content_id, content_type):
    data = {"trakt_username": username, "content_id": content_id, "content_type": content_type, "action": "block"}
    try: supabase.table("user_feedback").upsert(data, on_conflict="trakt_username, content_id").execute()
    except: pass

def get_user_blacklist(username, content_type):
    try:
        response = supabase.table("user_feedback").select("content_id").eq("trakt_username", username).eq("content_type", content_type).execute()
        return [x['content_id'] for x in response.data]
    except: return []

# === 5. INTERFACE ===

st.sidebar.title("🍿 CineGourmet")

with st.sidebar:
    st.header("⚙️ 1. Configurações")
    c_type = st.radio("Conteúdo", ["Filmes 🎬", "Séries 📺"], horizontal=True)
    api_type = "tv" if "Séries" in c_type else "movie"
    db_func = "match_tv_shows" if "Séries" in c_type else "match_movies"
    
    st.divider()
    username = st.text_input("Usuário Trakt:", placeholder="ex: lscastro")
    
    if st.button("🔄 Sincronizar"):
        if username:
            with st.spinner("Baixando dados..."):
                st.session_state['trakt_data'] = get_trakt_profile_data(username, api_type)
                st.session_state['app_blacklist'] = get_user_blacklist(username, api_type)
                st.success("Sincronizado!")
        else:
            st.warning("Digite um usuário.")
            
    if 'trakt_data' in st.session_state:
        d = st.session_state['trakt_data']
        # Mostra Loved e Total Vistos para confirmar leitura
        st.caption(f"✅ {len(d['loved'])} favoritos (9-10).")
        st.caption(f"👀 {len(d['watched_ids'])} itens já assistidos.")
    
    st.divider()
    st.subheader("📺 Meus Streamings")
    services_list = ["Netflix", "Amazon Prime Video", "Disney Plus", "Max", "Apple TV Plus", "Globoplay"]
    my_services = st.multiselect("Assinaturas:", services_list, default=services_list)
    threshold = st.slider("Ousadia", 0.0, 1.0, 0.45)

page = st.radio("Modo", ["🔍 Busca Rápida", "💎 Curadoria VIP"], horizontal=True, label_visibility="collapsed")
st.divider()

# ==============================================================================
# PÁGINA 1: BUSCA RÁPIDA (COM SCORE HÍBRIDO E IGNORE)
# ==============================================================================
if page == "🔍 Busca Rápida":
    st.title(f"🔍 Busca Turbo: {c_type}")
    
    context_str = ""
    full_blocked_ids = []
    if 'trakt_data' in st.session_state:
        context_str = build_context_string(st.session_state['trakt_data'])
        full_blocked_ids = st.session_state['trakt_data']['watched_ids'] + st.session_state.get('app_blacklist', [])
        st.info(f"🧠 Personalizado para **{username}**")

    query = st.text_area("O que você quer ver?", placeholder="Deixe vazio para 'Surpreenda-me'...")
    
    # Lógica do Botão Surpreenda-me
    btn_label = "🎲 Surpreenda-me" if not query else "🚀 Buscar"
    
    if st.button(btn_label):
        
        # Define o prompt baseado se tem texto ou não
        if not query:
            if not context_str:
                st.error("Para surpresas, preciso que você sincronize o Trakt primeiro!")
                st.stop()
            final_prompt = f"Analise este perfil: {context_str}. Recomende algo que ele vai AMAR baseado nas notas."
        else:
            final_prompt = f"Pedido: {query}. Contexto: {context_str}"
        
        with st.spinner("IA processando..."):
            vector = genai.embed_content(model="models/text-embedding-004", content=final_prompt)['embedding']
            
            resp = supabase.rpc(db_func, {
                "query_embedding": vector, 
                "match_threshold": threshold, 
                "match_count": 60,
                "filter_ids": full_blocked_ids
            }).execute()
            
            if resp.data:
                st.session_state['search_results'] = process_batch_parallel(resp.data, api_type, my_services, limit=8)
            else:
                st.session_state['search_results'] = []

    if 'search_results' in st.session_state and st.session_state['search_results']:
        
        if 'session_ignore' not in st.session_state: st.session_state['session_ignore'] = []
        
        visible_items = [i for i in st.session_state['search_results'] if i['id'] not in st.session_state['session_ignore']]
        
        if not visible_items:
            st.warning("Todos os itens foram ocultados ou nada encontrado.")
        else:
            for item in visible_items:
                c1, c2 = st.columns([1, 4])
                with c1:
                    if item['poster_path']: st.image(TMDB_IMAGE + item['poster_path'], use_container_width=True)
                    
                    if st.button("🙈 Nunca Mais", key=f"hide_{item['id']}"):
                        if username: save_block(username, item['id'], api_type)
                        st.session_state['session_ignore'].append(item['id'])
                        st.rerun()

                    if item.get('providers_flat'):
                        cols = st.columns(len(item['providers_flat']))
                        for i, p in enumerate(item['providers_flat']):
                            # CORREÇÃO DA INDENTAÇÃO AQUI:
                            if i < 4:
                                with cols[i]:
                                    st.image(TMDB_LOGO + p['logo_path'], width=25)
                
                with c2:
                    rating = float(item.get('vote_average', 0) or 0)
                    hybrid = int(item.get('hybrid_score', 0) * 100)
                    year = item.get('release_date', '')[:4] if item.get('release_date') else '????'
                    if 'first_air_date' in item: year = item.get('first_air_date', '')[:4]
                    
                    st.markdown(f"### {item['title']} ({year})")
                    st.caption(f"⭐ {rating:.1f}/10 | 🧠 CineScore: {hybrid}")
                    st.progress(hybrid, text="Qualidade Geral")
                    
                    expl = explain_choice(item['title'], context_str if context_str else "Geral", query if query else "Surpresa", item['overview'], rating)
                    st.success(f"💡 {expl}")
                    
                    b1, b2 = st.columns(2)
                    if item.get('trailer'): b1.link_button("▶️ Trailer", item['trailer'])
                    if item.get('trakt_url'): b2.link_button("📝 Trakt", item['trakt_url'])
                    
                    with st.expander("Sinopse"): st.write(item['overview'])
                st.divider()

# ==============================================================================
# PÁGINA 2: CURADORIA VIP
# ==============================================================================
elif page == "💎 Curadoria VIP":
    st.title(f"💎 Curadoria Fixa: {c_type}")
    
    if not username:
        st.error("Login necessário (Barra Lateral).")
    else:
        dashboard = load_user_dashboard(username)
        btn_text = "🔄 Atualizar Lista" if dashboard else "✨ Gerar Lista"
        
        if st.button(btn_text):
            if 'trakt_data' not in st.session_state:
                st.error("Sincronize o perfil primeiro!")
            else:
                with st.spinner("Gerando lista VIP..."):
                    context_str = build_context_string(st.session_state['trakt_data'])
                    full_blocked_ids = st.session_state['trakt_data']['watched_ids'] + st.session_state.get('app_blacklist', [])
                    
                    prompt = f"Analise: {context_str}. Recomende 30 obras-primas OBRIGATÓRIAS (Hidden Gems, Cults) não vistas."
                    vector = genai.embed_content(model="models/text-embedding-004", content=prompt)['embedding']
                    
                    resp = supabase.rpc(db_func, {
                        "query_embedding": vector, 
                        "match_threshold": threshold, 
                        "match_count": 120, 
                        "filter_ids": full_blocked_ids
                    }).execute()
                    
                    final_list = []
                    if resp.data:
                        final_list = process_batch_parallel(resp.data, api_type, my_services, limit=30)
                    
                    if final_list:
                        save_user_dashboard(username, final_list, {"type": c_type})
                        st.rerun()
                    else:
                        st.warning("Não consegui 30 filmes com seus filtros.")
        
        if dashboard and dashboard.get('curated_list'):
            st.divider()
            last_up = datetime.fromisoformat(dashboard['updated_at']).strftime('%d/%m %H:%M')
            st.caption(f"Atualizado em: {last_up}")
            
            items = dashboard['curated_list']
            cols = st.columns(3)
            for idx, item in enumerate(items):
                with cols[idx % 3]:
                    with st.container(border=True):
                        if item['poster_path']: st.image(TMDB_IMAGE + item['poster_path'], use_container_width=True)
                        st.markdown(f"**{item['title']}**")
                        rating = float(item.get('vote_average', 0) or 0)
                        
                        hybrid = int(item.get('hybrid_score', 0) * 100)
                        st.caption(f"⭐ {rating:.1f} | 🧠 {hybrid}")
                        
                        if item.get('providers_flat'):
                            p_cols = st.columns(len(item.get('providers_flat', [])))
                            for i, p in enumerate(item.get('providers_flat', [])):
                                # CORREÇÃO DA INDENTAÇÃO AQUI TAMBÉM:
                                if i < 4:
                                    with p_cols[i]:
                                        st.image(TMDB_LOGO + p['logo_path'], width=20)
                        
                        with st.expander("Detalhes"):
                            st.write(item['overview'])
                            if item.get('trailer'): st.link_button("Trailer", item['trailer'])
                            if item.get('trakt_url'): st.link_button("Trakt", item['trakt_url'])
