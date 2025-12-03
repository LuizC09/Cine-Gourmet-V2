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
st.set_page_config(page_title="CineGourmet Ultimate", page_icon="🍿", layout="wide")
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
    # ESTRUTURA NOVA (Se der erro 'positive', é porque o cache tá velho)
    data = {"history": [], "positive": [], "hated": [], "watched_ids": []}
    t_type = "shows" if content_type == "tv" else "movies"
    item_key = 'show' if content_type == "tv" else 'movie'

    try:
        # Histórico
        r_watched = session.get(f"https://api.trakt.tv/users/{username}/watched/{t_type}?limit=1000", headers=headers)
        if r_watched.status_code == 200:
            data["watched_ids"] = [i[item_key]['ids']['tmdb'] for i in r_watched.json() if i[item_key]['ids'].get('tmdb')]

        # Notas (Pega 200 para ter base sólida)
        r_ratings = session.get(f"https://api.trakt.tv/users/{username}/ratings/{t_type}?limit=200", headers=headers)
        if r_ratings.status_code == 200:
            for item in r_ratings.json():
                title = item[item_key]['title']
                rating = item['rating']
                
                # Agrupa tudo que é bom (7 a 10) na lista 'positive'
                if rating >= 7:
                    data["positive"].append((rating, f"{title} ({rating}/10)"))
                elif rating <= 5:
                    data["hated"].append(f"{title} ({rating}/10)")
            
            # Ordena por nota (10 primeiro)
            data["positive"].sort(key=lambda x: x[0], reverse=True)
            # Limpa para ficar só o texto
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
    # Pega top 40 positivos
    if data.get('positive'): 
        c += f"O USUÁRIO GOSTOU DESSES (Prioridade Alta para 9-10, Média para 7-8): {', '.join(data['positive'][:40])}. "
    if data.get('hated'): 
        c += f"O USUÁRIO DETESTOU (EVITAR ESTILO/TEMA): {', '.join(data['hated'][:20])}. "
    return c

def explain_choice(title, context_str, user_query, overview, rating):
    # PROMPT ANTI-ALUCINAÇÃO
    prompt = f"""
    Atue como um amigo cinéfilo SINCERO e 'pé no chão'.
    
    CONTEXTO:
    - O usuário gosta de: {context_str}
    - Ele pediu: "{user_query}"
    - Filme sugerido: "{title}" (Nota: {rating}/10).
    - Sinopse: {overview}
    
    REGRA DE OURO (LEIA COM ATENÇÃO):
    1. JAMAIS compare filmes infantis/comédias bobas com clássicos sérios (ex: Nunca compare nada com "Tropa de Elite", "Oppenheimer" ou "Poderoso Chefão" a menos que seja um drama policial/guerra do mesmo nível).
    2. Se o filme for "divertido mas bobo" (ex: Velozes e Furiosos, filmes de Youtuber), assuma isso! Diga "É para desligar o cérebro", "Curtição sem compromisso". Não finja que é arte.
    3. Se o filme for desconhecido mas tiver nota alta, seja cético: "Parece ser uma pérola escondida bem avaliada pelos fãs".
    4. NÃO use adjetivos exagerados ("Visceral", "Perturbador", "Obra-prima") para filmes nota 6 ou 7.
    
    SAÍDA:
    Escreva apenas UMA frase (max 25 palavras) explicando o apelo do filme de forma honesta.
    """
    try:
        model = genai.GenerativeModel('models/gemini-2.0-flash') 
        return model.generate_content(prompt).text.strip()
    except: return "Recomendação baseada no seu perfil."

def generate_marathon_plan(items, user_query):
    candidates = "\n".join([f"- {i['title']} (ID: {i['id']})" for i in items[:10]])
    prompt = f"""
    Contexto: "{user_query}".
    Lista:
    {candidates}
    
    Crie um Roteiro de Maratona com 3 filmes dessa lista para ver em sequência lógica.
    Retorne formato lista: 1. Nome - Motivo.
    """
    try:
        model = genai.GenerativeModel('models/gemini-2.0-flash')
        return model.generate_content(prompt).text.strip()
    except: return "Erro ao gerar maratona."

def convert_list_to_text(items, username):
    txt = f"🎬 Curadoria CineGourmet para {username}\n\n"
    for i, item in enumerate(items):
        rating = float(item.get('vote_average', 0) or 0)
        year = item.get('release_date', '')[:4] if item.get('release_date') else ''
        if 'first_air_date' in item: year = item.get('first_air_date', '')[:4]
        txt += f"{i+1}. {item['title']} ({year}) - ⭐ {rating:.1f}\n"
    return txt

# === 3. LÓGICA HÍBRIDA & PARALELA ===

def calculate_hybrid_score(item):
    """CineScore v4: Menos Caos, Mais Similaridade"""
    sim_score = float(item.get('similarity', 0))
    vote = float(item.get('vote_average', 0) or 0)
    
    # Peso quadrático na nota (8.0 vale muito mais que 6.0)
    rating_score = (vote / 10.0) ** 2
    
    pop = float(item.get('popularity', 0) or 0)
    pop_score = min(pop / 1000.0, 1.0)
    
    # Caos reduzido para 5% (só para desempate)
    chaos = random.random() * 0.05
    
    # 70% Semântica (Foco no pedido!), 20% Qualidade, 5% Fama, 5% Caos
    return (sim_score * 0.70) + (rating_score * 0.20) + (pop_score * 0.05) + chaos

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
    
    if st.button("🔄 Sincronizar", help="Baixa seu histórico e notas (Considera notas 7 a 10)."):
        if username:
            # === AUTO-LIMPEZA DO CACHE VELHO ===
            # Se forçar sincronização, limpa o estado para evitar erro de chave
            if 'trakt_data' in st.session_state: del st.session_state['trakt_data']
            
            with st.spinner("Baixando dados..."):
                st.session_state['trakt_data'] = get_trakt_profile_data(username, api_type)
                st.session_state['app_blacklist'] = get_user_blacklist(username, api_type)
                st.success("Sincronizado!")
                st.rerun() # Recarrega a página para aplicar
        else:
            st.warning("Digite um usuário.")
            
    if 'trakt_data' in st.session_state:
        d = st.session_state['trakt_data']
        # Proteção contra erro de chave (Retrocompatibilidade)
        if 'positive' not in d:
            st.warning("⚠️ Dados antigos detectados. Clique em 'Sincronizar' novamente.")
        else:
            st.caption(f"✅ {len(d['positive'])} filmes curtidos (7-10).")
            st.caption(f"👀 {len(d['watched_ids'])} vistos.")
    
    st.divider()
    st.subheader("📺 Meus Streamings")
    services_list = ["Netflix", "Amazon Prime Video", "Disney Plus", "Max", "Apple TV Plus", "Globoplay"]
    my_services = st.multiselect("Assinaturas:", services_list, default=services_list)
    threshold = st.slider("Ousadia", 0.0, 1.0, 0.45, help="Baixo: Literal. Alto: Criativo.")

page = st.radio("Modo", ["🔍 Busca Rápida", "🧞 Akinator (Quiz)", "💎 Curadoria VIP"], horizontal=True, label_visibility="collapsed")
st.divider()

# ==============================================================================
# PÁGINA 1: BUSCA RÁPIDA
# ==============================================================================
if page == "🔍 Busca Rápida":
    st.title(f"🔍 Busca Turbo: {c_type}")
    
    context_str = ""
    full_blocked_ids = []
    if 'trakt_data' in st.session_state:
        # Verifica integridade dos dados
        if 'positive' in st.session_state['trakt_data']:
            context_str = build_context_string(st.session_state['trakt_data'])
            full_blocked_ids = st.session_state['trakt_data']['watched_ids'] + st.session_state.get('app_blacklist', [])
            st.info(f"🧠 Personalizado para **{username}**")
        else:
            st.warning("⚠️ Perfil desatualizado. Clique em Sincronizar na lateral.")

    query = st.text_area("O que você quer ver?", placeholder="Deixe vazio para 'Surpreenda-me'...")
    
    btn_label = "🎲 Surpreenda-me" if not query else "🚀 Buscar"
    help_text = "Modo Automático: Baseado na sua psicologia." if not query else "Modo Busca: Cruza pedido com seu perfil."
    
    if st.button(btn_label, help=help_text):
        if not query and not context_str:
            st.error("Para surpresas, preciso que você sincronize o Trakt primeiro!")
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
        if st.button("🍿 Gerar Roteiro de Maratona (3 Filmes)", help="Cria uma sequência lógica com 3 filmes dos resultados."):
            with st.spinner("Criando..."):
                plan = generate_marathon_plan(st.session_state['search_results'], st.session_state.get('current_query', ''))
                st.markdown("### 🎬 Roteiro Sugerido")
                st.success(plan)
        st.divider()
        
        if 'session_ignore' not in st.session_state: st.session_state['session_ignore'] = []
        visible_items = [i for i in st.session_state['search_results'] if i['id'] not in st.session_state['session_ignore']]
        
        if not visible_items:
            st.warning("Sem resultados.")
        else:
            for item in visible_items:
                c1, c2 = st.columns([1, 4])
                with c1:
                    if item['poster_path']: st.image(TMDB_IMAGE + item['poster_path'], use_container_width=True)
                    
                    if st.button("🙈 Nunca Mais", key=f"hide_{item['id']}", help="Bloqueia este título ETERNAMENTE."):
                        if username: save_block(username, item['id'], api_type)
                        st.session_state['session_ignore'].append(item['id'])
                        st.rerun()

                    if item.get('providers_flat'):
                        cols = st.columns(len(item['providers_flat']))
                        for i, p in enumerate(item['providers_flat']):
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
                    
                    expl = explain_choice(item['title'], context_str if context_str else "Geral", st.session_state.get('current_query', ''), item['overview'], rating)
                    st.success(f"💡 {expl}")
                    
                    b1, b2 = st.columns(2)
                    if item.get('trailer'): b1.link_button("▶️ Trailer", item['trailer'], help="Ver no YouTube")
                    if item.get('trakt_url'): b2.link_button("📝 Trakt", item['trakt_url'], help="Abrir página do Trakt")
                    
                    with st.expander("Detalhes & Análise IA"):
                        if item.get('ai_analysis'):
                            st.info(f"🧠 **CineGourmet Brain:**\n\n{item['ai_analysis']}")
                        st.write(f"**Sinopse:** {item['overview']}")
                st.divider()

# ==============================================================================
# PÁGINA 2: AKINATOR (QUIZ)
# ==============================================================================
elif page == "🧞 Akinator (Quiz)":
    st.title(f"🧞 Akinator: {c_type}")
    st.caption("Responda apenas o que você fizer questão. O que deixar em branco, a IA decide.")

    context_str = ""
    full_blocked_ids = []
    if 'trakt_data' in st.session_state and 'positive' in st.session_state['trakt_data']:
        context_str = build_context_string(st.session_state['trakt_data'])
        full_blocked_ids = st.session_state['trakt_data']['watched_ids'] + st.session_state.get('app_blacklist', [])

    with st.form("akinator_form"):
        c1, c2 = st.columns(2)
        with c1:
            q_mood = st.multiselect("🎭 Qual a Vibe?", ["Tenso/Assustador", "Pra Chorar", "Rir Alto", "Refletir/Cabeça", "Adrenalina Pura", "Leve/Feel Good", "Sombrio/Noir"])
            q_era = st.select_slider("🕰️ Época Preferida", options=["Clássicos P&B", "Anos 70/80", "Anos 90/00", "Moderno (2010+)", "Lançamento Recente"], value=("Anos 70/80", "Lançamento Recente"))
        with c2:
            q_pace = st.radio("⚡ Ritmo", ["Tanto faz", "Lento e Atmosférico (Slow Burn)", "Rápido e Frenético"], horizontal=True)
            q_complexity = st.radio("🧩 Complexidade", ["Tanto faz", "Desligar o cérebro (Pipoca)", "Plot Twists e Mistério"], horizontal=True)
        
        st.divider()
        q_extra = st.text_input("Algum detalhe extra? (Opcional)", placeholder="ex: quero que se passe no espaço, ou tenha zumbis...")
        
        submit = st.form_submit_button("🧞 Adivinhe meu desejo")

    if submit:
        akinator_prompt = f"""
        O usuário preencheu um quiz de preferências. Encontre o filme perfeito.
        RESPOSTAS DO QUIZ:
        - Vibe desejada: {', '.join(q_mood) if q_mood else 'Qualquer uma'}
        - Época: Entre {q_era[0]} e {q_era[1]}
        - Ritmo: {q_pace}
        - Complexidade: {q_complexity}
        - Detalhes extras: {q_extra}
        
        PERFIL DO USUÁRIO (TRAKT):
        {context_str}
        
        INSTRUÇÃO: Combine as respostas do quiz com o gosto pessoal do Trakt.
        """
        
        with st.spinner("O gênio está pensando..."):
            vector = genai.embed_content(model="models/text-embedding-004", content=akinator_prompt)['embedding']
            resp = supabase.rpc(db_func, {"query_embedding": vector, "match_threshold": threshold, "match_count": 60, "filter_ids": full_blocked_ids}).execute()
            
            if resp.data:
                st.session_state['search_results'] = process_batch_parallel(resp.data, api_type, my_services, limit=10)
                st.session_state['current_query'] = "Quiz Akinator"
                st.rerun()
            else:
                st.error("O gênio não encontrou nada com essas especificações tão rígidas!")

    if 'search_results' in st.session_state and st.session_state['search_results'] and st.session_state.get('current_query') == "Quiz Akinator":
        st.divider()
        st.subheader("🔮 Previsões do Gênio")
        
        if 'session_ignore' not in st.session_state: st.session_state['session_ignore'] = []
        visible_items = [i for i in st.session_state['search_results'] if i['id'] not in st.session_state['session_ignore']]
        
        for item in visible_items:
            c1, c2 = st.columns([1, 4])
            with c1:
                if item['poster_path']: st.image(TMDB_IMAGE + item['poster_path'], use_container_width=True)
                if st.button("🙈 Nunca Mais", key=f"hide_ak_{item['id']}", help="Bloquear"):
                    if username: save_block(username, item['id'], api_type)
                    st.session_state['session_ignore'].append(item['id'])
                    st.rerun()
                if item.get('providers_flat'):
                    cols = st.columns(len(item['providers_flat']))
                    for i, p in enumerate(item['providers_flat']):
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
                
                expl = explain_choice(item['title'], context_str if context_str else "Geral", "Quiz do Akinator", item['overview'], rating)
                st.success(f"💡 {expl}")
                
                b1, b2 = st.columns(2)
                if item.get('trailer'): b1.link_button("▶️ Trailer", item['trailer'])
                if item.get('trakt_url'): b2.link_button("📝 Trakt", item['trakt_url'])
                
                with st.expander("Detalhes & Análise IA"):
                    if item.get('ai_analysis'):
                        st.info(f"🧠 **CineGourmet Brain:**\n\n{item['ai_analysis']}")
                    st.write(f"**Sinopse:** {item['overview']}")
            st.divider()

# ==============================================================================
# PÁGINA 3: CURADORIA VIP
# ==============================================================================
elif page == "💎 Curadoria VIP":
    st.title(f"💎 Curadoria Fixa: {c_type}")
    
    if not username:
        st.error("Login necessário (Barra Lateral).")
    else:
        dashboard = load_user_dashboard(username)
        btn_text = "🔄 Atualizar Lista" if dashboard else "✨ Gerar Lista"
        
        if st.button(btn_text, help="Gera/Renova uma lista fixa de 30 recomendações."):
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
            text_data = convert_list_to_text(dashboard['curated_list'], username)
            st.download_button("📤 Baixar Lista", text_data, file_name="minha_curadoria.txt", help="Baixa arquivo de texto para WhatsApp.")
            
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
                                if i < 4: 
                                    with p_cols[i]:
                                        st.image(TMDB_LOGO + p['logo_path'], width=20)
                        
                        with st.expander("Detalhes"):
                            st.write(item['overview'])
                            if item.get('trailer'): st.link_button("Trailer", item['trailer'])
                            if item.get('trakt_url'): st.link_button("Trakt", item['trakt_url'])
