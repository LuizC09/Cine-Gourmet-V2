import streamlit as st
import google.generativeai as genai
import requests
import pandas as pd
from supabase import create_client
from datetime import datetime
import concurrent.futures
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import random

# ==============================================================================
# 1. CONFIGURAÇÃO E SEGREDOS
# ==============================================================================
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

# ==============================================================================
# 2. SESSÃO E CACHE
# ==============================================================================

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
    data = {"history": [], "positive": [], "hated": [], "watched_ids": []}
    t_type = "shows" if content_type == "tv" else "movies"
    item_key = 'show' if content_type == "tv" else 'movie'

    try:
        r_watched = session.get(f"https://api.trakt.tv/users/{username}/watched/{t_type}?limit=1000", headers=headers)
        if r_watched.status_code == 200:
            data["watched_ids"] = [i[item_key]['ids']['tmdb'] for i in r_watched.json() if i[item_key]['ids'].get('tmdb')]

        r_ratings = session.get(f"https://api.trakt.tv/users/{username}/ratings/{t_type}?limit=200", headers=headers)
        if r_ratings.status_code == 200:
            for item in r_ratings.json():
                title = item[item_key]['title']
                rating = item['rating']
                if rating >= 7:
                    data["positive"].append((rating, f"{title} ({rating}/10)"))
                elif rating <= 5:
                    data["hated"].append(f"{title} ({rating}/10)")
            
            data["positive"].sort(key=lambda x: x[0], reverse=True)
            data["positive"] = [x[1] for x in data["positive"]]
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
    if data.get('positive'): 
        c += f"O USUÁRIO GOSTOU DESSES (9-10 é amor, 7-8 é curtiu): {', '.join(data['positive'][:40])}. "
    if data.get('hated'): 
        c += f"O USUÁRIO DETESTOU/EVITAR (1-5): {', '.join(data['hated'][:20])}. "
    return c

# --- NOVA FUNÇÃO DE BUSCA DIRETA (PARA O ORÁCULO) ---
@st.cache_data(ttl=3600)
def search_tmdb_by_name(query, content_type):
    """Busca um filme/série específico pelo nome no TMDB"""
    url = f"https://api.themoviedb.org/3/search/{content_type}"
    params = {"api_key": TMDB_API_KEY, "query": query, "language": "pt-BR", "page": 1}
    try:
        r = session.get(url, params=params)
        if r.status_code == 200:
            return r.json().get('results', [])
    except: pass
    return []

# --- NOVA FUNÇÃO DE ANÁLISE DO ORÁCULO ---
def oracle_analysis(target_item, user_context):
    prompt = f"""
    Atue como um algoritmo de compatibilidade de cinema.
    
    PERFIL DO USUÁRIO:
    {user_context}
    
    ALVO DA ANÁLISE:
    Título: {target_item['title']}
    Sinopse: {target_item['overview']}
    Nota Pública: {target_item.get('vote_average')}
    
    TAREFA:
    Calcule a compatibilidade entre o usuário e este título específico.
    
    SAÍDA ESPERADA (Responda EXATAMENTE neste formato de 3 linhas):
    Linha 1: [Número de 0 a 100 representando a porcentagem de match]
    Linha 2: [Veredito curto: "Vai amar", "Arriscado", "Pule", etc.]
    Linha 3: [Explicação de 1 frase citando filmes do perfil que se parecem ou diferem]
    """
    try:
        model = genai.GenerativeModel('models/gemini-2.0-flash')
        return model.generate_content(prompt).text.strip()
    except: return "50\nIncerto\nNão consegui analisar o perfil."

def explain_choice(title, context_str, user_query, overview, rating):
    prompt = f"""
    Atue como crítico de cinema. PERFIL: {context_str}. PEDIDO: "{user_query}". 
    OBRA: "{title}" ({rating}/10). SINOPSE: {overview}.
    TAREFA: Frase única e persuasiva conectando a obra ao perfil.
    """
    try:
        return genai.GenerativeModel('models/gemini-2.0-flash').generate_content(prompt).text.strip()
    except: return "Recomendação baseada no seu perfil."

def generate_marathon_plan(items, user_query):
    candidates = "\n".join([f"- {i['title']}" for i in items[:10]])
    prompt = f"Crie roteiro de maratona (3 filmes) lógico para: {user_query}. Lista: {candidates}."
    try:
        return genai.GenerativeModel('models/gemini-2.0-flash').generate_content(prompt).text.strip()
    except: return "Erro."

def convert_list_to_text(items, username):
    txt = f"🎬 Curadoria CineGourmet para {username}\n\n"
    for i, item in enumerate(items):
        rating = float(item.get('vote_average', 0) or 0)
        txt += f"{i+1}. {item['title']} - ⭐ {rating:.1f}\n"
    return txt

# === 3. LÓGICA HÍBRIDA & PARALELA ===

def calculate_hybrid_score(item):
    sim_score = float(item.get('similarity', 0))
    vote = float(item.get('vote_average', 0) or 0)
    rating_score = (vote / 10.0) ** 2 
    pop = float(item.get('popularity', 0) or 0)
    pop_score = min(pop / 1000.0, 1.0)
    chaos = random.random() * 0.05
    return (sim_score * 0.70) + (rating_score * 0.20) + (pop_score * 0.05) + chaos

def process_single_item(item, api_type, my_services):
    is_ok, flat, rent = get_watch_providers(item['id'], api_type)
    
    has_service = False
    if my_services:
        avail_names = [p['provider_name'] for p in flat]
        has_service = any(s in avail_names for s in my_services)
        if not has_service and not rent: return None
    else: has_service = True
    
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
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(process_single_item, item, api_type, my_services) for item in items]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: results.append(res)
    results.sort(key=lambda x: x['hybrid_score'], reverse=True)
    return results[:limit]

# === 4. PERSISTÊNCIA ===

def load_user_dashboard(username):
    response = supabase.table("user_dashboards").select("*").eq("trakt_username", username).execute()
    return response.data[0] if response.data else None

def save_user_dashboard(username, curated_list, prefs):
    data = {"trakt_username": username, "curated_list": curated_list, "preferences": prefs, "updated_at": datetime.now().isoformat()}
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
    st.header("⚙️ Configuração")
    c_type = st.radio("Conteúdo", ["Filmes 🎬", "Séries 📺"], horizontal=True)
    api_type = "tv" if "Séries" in c_type else "movie"
    db_func = "match_tv_shows" if "Séries" in c_type else "match_movies"
    
    st.divider()
    username = st.text_input("Usuário Trakt:", placeholder="ex: lscastro")
    
    if st.button("🔄 Sincronizar", help="Baixa histórico e notas (7+)."):
        if username:
            # Limpa cache para forçar recarga
            if 'trakt_data' in st.session_state: del st.session_state['trakt_data']
            with st.spinner("Baixando dados..."):
                st.session_state['trakt_data'] = get_trakt_profile_data(username, api_type)
                st.session_state['app_blacklist'] = get_user_blacklist(username, api_type)
                st.success("Sincronizado!")
                st.rerun()
        else: st.warning("Digite um usuário.")
            
    if 'trakt_data' in st.session_state:
        d = st.session_state['trakt_data']
        if 'positive' in d:
            st.caption(f"✅ {len(d['positive'])} curtidos. 👀 {len(d['watched_ids'])} vistos.")
    
    st.divider()
    st.subheader("📺 Streamings")
    services_list = ["Netflix", "Amazon Prime Video", "Disney Plus", "Max", "Apple TV Plus", "Globoplay"]
    my_services = st.multiselect("Assinaturas:", services_list, default=services_list)
    threshold = st.slider("Ousadia", 0.0, 1.0, 0.45)

# MENU PRINCIPAL COM 4 OPÇÕES
page = st.radio("Modo", ["🔍 Busca Rápida", "🔮 O Oráculo", "🧞 Akinator", "💎 Curadoria VIP"], horizontal=True, label_visibility="collapsed")
st.divider()

# ==============================================================================
# PÁGINA 1: BUSCA RÁPIDA
# ==============================================================================
if page == "🔍 Busca Rápida":
    st.title(f"🔍 Busca Turbo: {c_type}")
    
    context_str = ""
    full_blocked_ids = []
    if 'trakt_data' in st.session_state and 'positive' in st.session_state['trakt_data']:
        context_str = build_context_string(st.session_state['trakt_data'])
        full_blocked_ids = st.session_state['trakt_data']['watched_ids'] + st.session_state.get('app_blacklist', [])
        st.info(f"🧠 Personalizado para **{username}**")

    query = st.text_area("O que você quer ver?", placeholder="Deixe vazio para 'Surpreenda-me'...")
    btn_label = "🎲 Surpreenda-me" if not query else "🚀 Buscar"
    
    if st.button(btn_label):
        if not query and not context_str:
            st.error("Sincronize o Trakt primeiro!")
            st.stop()
            
        final_prompt = f"Pedido: {query}. Contexto: {context_str}" if query else f"Analise: {context_str}. Recomende algo que ele vai AMAR."
        
        with st.spinner("IA processando..."):
            vector = genai.embed_content(model="models/text-embedding-004", content=final_prompt)['embedding']
            resp = supabase.rpc(db_func, {"query_embedding": vector, "match_threshold": threshold, "match_count": 60, "filter_ids": full_blocked_ids}).execute()
            
            if resp.data:
                st.session_state['search_results'] = process_batch_parallel(resp.data, api_type, my_services, limit=10)
                st.session_state['current_query'] = query if query else "Surpresa"
            else:
                st.session_state['search_results'] = []

    if 'search_results' in st.session_state and st.session_state['search_results']:
        if st.button("🍿 Gerar Roteiro de Maratona (3 Filmes)"):
            with st.spinner("Criando..."):
                plan = generate_marathon_plan(st.session_state['search_results'], st.session_state.get('current_query', ''))
                st.success(plan)
        st.divider()
        
        if 'session_ignore' not in st.session_state: st.session_state['session_ignore'] = []
        visible_items = [i for i in st.session_state['search_results'] if i['id'] not in st.session_state['session_ignore']]
        
        if not visible_items: st.warning("Sem resultados.")
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
                            if i < 4: 
                                with cols[i]: st.image(TMDB_LOGO + p['logo_path'], width=25)
                with c2:
                    rating = float(item.get('vote_average', 0) or 0)
                    hybrid = int(item.get('hybrid_score', 0) * 100)
                    year = item.get('release_date', '')[:4] if item.get('release_date') else '????'
                    if 'first_air_date' in item: year = item.get('first_air_date', '')[:4]
                    
                    st.markdown(f"### {item['title']} ({year})")
                    st.caption(f"⭐ {rating:.1f}/10 | 🧠 CineScore: {hybrid}")
                    st.progress(hybrid, text="Qualidade Geral")
                    
                    expl = explain_choice(item['title'], context_str if context_str else "Geral", st.session_state.get('current_query', ''), item['overview'], rating)
                    st.success(f"💡 {expl}")
                    
                    b1, b2 = st.columns(2)
                    if item.get('trailer'): b1.link_button("▶️ Trailer", item['trailer'])
                    if item.get('trakt_url'): b2.link_button("📝 Trakt", item['trakt_url'])
                    with st.expander("Detalhes"):
                         if item.get('ai_analysis'): st.info(f"🧠 {item['ai_analysis']}")
                         st.write(item['overview'])
                st.divider()

# ==============================================================================
# PÁGINA 2: O ORÁCULO (NOVO!)
# ==============================================================================
elif page == "🔮 O Oráculo":
    st.title(f"🔮 Oráculo de Compatibilidade")
    st.caption(f"Digite o nome de um {c_type[:-2]} e a IA dirá se combina com você.")
    
    if 'trakt_data' not in st.session_state:
        st.error("Sincronize o perfil na barra lateral para o Oráculo funcionar!")
    else:
        oracle_query = st.text_input("Nome do título:", placeholder="ex: Interestelar")
        
        if oracle_query and st.button("Consultar Oráculo"):
            with st.spinner("Buscando dados e analisando seu DNA..."):
                # 1. Busca no TMDB
                search_results = search_tmdb_by_name(oracle_query, api_type)
                
                if not search_results:
                    st.error("Não encontrei esse título no TMDB.")
                else:
                    target = search_results[0] # Pega o primeiro
                    
                    # 2. Processa detalhes (Streaming etc)
                    target = process_single_item(target, api_type, my_services) or target # Se não tiver streaming, mostra mesmo assim
                    
                    # 3. Consulta a IA
                    context_str = build_context_string(st.session_state['trakt_data'])
                    oracle_res = oracle_analysis(target, context_str)
                    
                    # Tenta parsear a resposta (Linha 1 = Score)
                    lines = oracle_res.split('\n')
                    try:
                        score = int(lines[0].replace('%', '').strip())
                        verdict = lines[1] if len(lines) > 1 else ""
                        reason = lines[2] if len(lines) > 2 else ""
                    except:
                        score = 50
                        verdict = "Incerto"
                        reason = oracle_res
                    
                    # 4. Exibe Resultado
                    st.divider()
                    c1, c2 = st.columns([1, 2])
                    with c1:
                         if target.get('poster_path'): 
                             st.image(TMDB_IMAGE + target['poster_path'], use_container_width=True)
                    with c2:
                        st.subheader(f"{target['title']} (Match: {score}%)")
                        st.progress(score)
                        
                        if score > 80: st.success(f"🤩 Veredito: {verdict}")
                        elif score > 50: st.warning(f"🤔 Veredito: {verdict}")
                        else: st.error(f"💀 Veredito: {verdict}")
                        
                        st.info(f"🧠 {reason}")
                        
                        st.caption(f"Nota Pública: {target.get('vote_average')}")
                        st.write(target['overview'])

# ==============================================================================
# PÁGINA 3: AKINATOR
# ==============================================================================
elif page == "🧞 Akinator (Quiz)":
    st.title(f"🧞 Akinator: {c_type}")
    context_str = ""
    full_blocked_ids = []
    if 'trakt_data' in st.session_state and 'positive' in st.session_state['trakt_data']:
        context_str = build_context_string(st.session_state['trakt_data'])
        full_blocked_ids = st.session_state['trakt_data']['watched_ids'] + st.session_state.get('app_blacklist', [])

    with st.form("akinator_form"):
        c1, c2 = st.columns(2)
        with c1:
            q_mood = st.multiselect("🎭 Vibe", ["Tenso", "Chorar", "Rir", "Cabeça", "Adrenalina", "Leve", "Sombrio"])
            q_era = st.select_slider("🕰️ Época", options=["Clássicos", "70/80", "90/00", "Moderno", "Recente"], value=("70/80", "Recente"))
        with c2:
            q_pace = st.radio("⚡ Ritmo", ["Tanto faz", "Lento", "Rápido"], horizontal=True)
            q_comp = st.radio("🧩 Complexidade", ["Tanto faz", "Pipoca", "Cabeça"], horizontal=True)
        st.divider()
        q_extra = st.text_input("Extra", placeholder="ex: zumbis...")
        submit = st.form_submit_button("🧞 Adivinhe")

    if submit:
        prompt = f"Quiz: Vibe {q_mood}, Época {q_era}, Ritmo {q_pace}, Nível {q_comp}, Extra {q_extra}. Perfil: {context_str}"
        with st.spinner("O gênio está pensando..."):
            vector = genai.embed_content(model="models/text-embedding-004", content=prompt)['embedding']
            resp = supabase.rpc(db_func, {"query_embedding": vector, "match_threshold": threshold, "match_count": 60, "filter_ids": full_blocked_ids}).execute()
            if resp.data:
                st.session_state['search_results'] = process_batch_parallel(resp.data, api_type, my_services, limit=10)
                st.session_state['current_query'] = "Quiz Akinator"
                st.rerun()
            else: st.error("Nada encontrado!")

    if 'search_results' in st.session_state and st.session_state.get('current_query') == "Quiz Akinator":
        # Reutiliza loop de exibição (código simplificado para caber, use o mesmo do bloco de Busca Rápida acima se quiser igual)
        for item in st.session_state['search_results']:
             with st.container(border=True):
                 c1, c2 = st.columns([1,4])
                 with c1: st.image(TMDB_IMAGE + item['poster_path'])
                 with c2:
                     st.subheader(item['title'])
                     st.info(explain_choice(item['title'], context_str, "Quiz", item['overview'], 0))

# ==============================================================================
# PÁGINA 4: CURADORIA VIP
# ==============================================================================
elif page == "💎 Curadoria VIP":
    st.title(f"💎 Curadoria Fixa: {c_type}")
    if not username: st.error("Login necessário.")
    else:
        dashboard = load_user_dashboard(username)
        btn_text = "🔄 Atualizar Lista" if dashboard else "✨ Gerar Lista"
        
        if st.button(btn_text):
            if 'trakt_data' not in st.session_state: st.error("Sincronize primeiro!")
            else:
                with st.spinner("Gerando..."):
                    context_str = build_context_string(st.session_state['trakt_data'])
                    blocked = st.session_state['trakt_data']['watched_ids'] + st.session_state.get('app_blacklist', [])
                    prompt = f"Analise: {context_str}. Recomende 30 obras-primas não vistas."
                    vector = genai.embed_content(model="models/text-embedding-004", content=prompt)['embedding']
                    resp = supabase.rpc(db_func, {"query_embedding": vector, "match_threshold": threshold, "match_count": 120, "filter_ids": blocked}).execute()
                    
                    final = []
                    if resp.data: final = process_batch_parallel(resp.data, api_type, my_services, limit=30)
                    
                    if final:
                        save_user_dashboard(username, final, {"type": c_type})
                        st.rerun()
        
        if dashboard and dashboard.get('curated_list'):
            st.divider()
            text = convert_list_to_text(dashboard['curated_list'], username)
            st.download_button("📤 Baixar", text, file_name="lista.txt")
            
            items = dashboard['curated_list']
            cols = st.columns(3)
            for idx, item in enumerate(items):
                with cols[idx % 3]:
                    with st.container(border=True):
                        if item['poster_path']: st.image(TMDB_IMAGE + item['poster_path'])
                        st.markdown(f"**{item['title']}**")
                        if item.get('providers_flat'):
                             for p in item['providers_flat'][:3]: st.image(TMDB_LOGO + p['logo_path'], width=20)
                        with st.expander("Detalhes"): st.write(item['overview'])
