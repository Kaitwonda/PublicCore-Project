# autonomous_learner.py



import time

import random

from pathlib import Path

import json

from collections import defaultdict, Counter # Added Counter

from urllib.parse import urljoin, urlparse



from processing_nodes import LogicNode, SymbolicNode, DynamicBridge, CurriculumManager

import web_parser

import parser as P_Parser



# --- Configuration ---

PHASE_URL_SOURCES = { # Keep your existing PHASE_URL_SOURCES

    1: ["https://en.wikipedia.org/wiki/Algorithm", "https://en.wikipedia.org/wiki/Computer_programming"],

    2: ["https://en.wikipedia.org/wiki/Emotion", "https://en.wikipedia.org/wiki/Mythology"],

    3: ["https://en.wikipedia.org/wiki/History_of_science", "https://en.wikipedia.org/wiki/World_history"],

    4: ["https://en.wikipedia.org/wiki/Philosophy_of_mind", "https://en.wikipedia.org/wiki/Quantum_mechanics"]

}

deferred_urls_by_phase = defaultdict(list)

visited_urls_globally = set()

DATA_DIR = Path("data")



# --- Helper Functions ---

def initialize_data_files_if_needed(): # Keep your existing function

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    data_files_to_check = {

        "data/vector_memory.json": [], "data/symbol_memory.json": {},

        "data/symbol_occurrence_log.json": {"entries": []},

        "data/symbol_emotion_map.json": {}, "data/meta_symbols.json": {},

        "data/trail_log.json": [], "data/deferred_urls_log.json": {},

        "data/seed_symbols.json": {

            "🔥": {"name": "Fire", "keywords": ["fire", "flame", "computation", "logic"], "core_meanings": ["heat"], "emotions": ["anger"], "archetypes": ["destroyer"], "learning_phase": 0, "resonance_weight": 0.7},

            "💧": {"name": "Water", "keywords": ["water", "liquid", "data", "flow"], "core_meanings": ["flow"], "emotions": ["calm"], "archetypes": ["healer"], "learning_phase": 0, "resonance_weight": 0.7},

            "💻": {"name": "Computer", "keywords": ["computer", "computation", "cpu", "binary", "code", "algorithm", "system", "architecture"], "core_meanings": ["processing", "logic unit"], "emotions": ["neutral", "focus"], "archetypes": ["tool", "oracle"], "learning_phase": 0, "resonance_weight": 0.8}

        }

    }

    for file_path_str, default_content in data_files_to_check.items():

        file_path = Path(file_path_str)

        if not file_path.exists() or file_path.stat().st_size == 0:

            with open(file_path, "w", encoding="utf-8") as f: json.dump(default_content, f, indent=2, ensure_ascii=False)

        else:

            if file_path_str not in ["data/seed_symbols.json"]:

                try:

                    with open(file_path, "r", encoding="utf-8") as f: json.load(f)

                except json.JSONDecodeError:

                    with open(file_path, "w", encoding="utf-8") as f: json.dump(default_content, f, indent=2, ensure_ascii=False)



# MODIFIED: score_text_against_keywords (generic scorer)

def score_text_against_keywords(text_content, primary_keywords, secondary_keywords=None, anti_keywords=None):

    if not text_content or not isinstance(text_content, str): return 0.0, 0

    text_lower = text_content.lower()

    score = 0.0

    primary_matches = 0

    

    for kw in primary_keywords:

        if kw.lower() in text_lower: score += 2.0; primary_matches += 1

    if secondary_keywords:

        for kw in secondary_keywords:

            if kw.lower() in text_lower: score += 1.0

    if anti_keywords:

        for kw in anti_keywords:

            if kw.lower() in text_lower: score -= 3.0

    return score, primary_matches



# MODIFIED: evaluate_link_action now also considers session hot keywords

def evaluate_link_action(link_anchor_text, link_url,

                         current_processing_phase_num, curriculum_manager,

                         session_hot_keywords=None): # New: session_hot_keywords

    """

    Evaluates a link based on static phase keywords and dynamic session hot keywords.

    Returns: (action_type, target_phase_num_for_storage, final_priority_score)

    """

    current_phase_directives = curriculum_manager.get_processing_directives(current_processing_phase_num)

    

    # 1. Static Phase Score

    static_score, static_primary_matches = score_text_against_keywords(

        link_anchor_text,

        current_phase_directives.get("phase_keywords_primary", []),

        current_phase_directives.get("phase_keywords_secondary", []),

        current_phase_directives.get("phase_keywords_anti", [])

    )



    # 2. Dynamic Session Score (if hot keywords are available)

    dynamic_score = 0.0

    if session_hot_keywords: # session_hot_keywords is a Counter or set

        dynamic_matches = 0

        # Give more weight to more frequent hot keywords if it's a Counter

        for hot_kw, freq in session_hot_keywords.items() if isinstance(session_hot_keywords, Counter) else [(kw, 1) for kw in session_hot_keywords]:

            if hot_kw.lower() in link_anchor_text.lower():

                dynamic_score += (1.0 * freq) # Simple example: add frequency

                dynamic_matches +=1

        # Normalize dynamic_score if it can get too large, e.g., dynamic_score = min(dynamic_score, 5.0)

        dynamic_score = min(dynamic_score, current_phase_directives.get("max_dynamic_link_score_bonus", 5.0))





    # 3. Combined Priority Score (Tune weights as needed)

    # Weights can also be part of directives

    weight_static = current_phase_directives.get("link_score_weight_static", 0.6)

    weight_dynamic = current_phase_directives.get("link_score_weight_dynamic", 0.4)

    final_priority_score = (static_score * weight_static) + (dynamic_score * weight_dynamic)



    # Decision to follow now (based on combined score and static primary matches)

    min_primary_follow = current_phase_directives.get("phase_min_primary_keyword_matches_for_link_follow", 1) # Allow 1 primary if score is high

    min_total_score_follow = current_phase_directives.get("phase_min_total_keyword_score_for_link_follow", 2.5) # Adjusted threshold



    if static_primary_matches >= min_primary_follow and final_priority_score >= min_total_score_follow:

        return "FOLLOW_NOW", current_processing_phase_num, final_priority_score



    # If not following now, check for deferral (based on static score against other phases)

    best_future_phase_score = -float('inf')

    best_future_phase_num = None

    for future_phase_idx in range(1, curriculum_manager.get_max_phases() + 1):

        if future_phase_idx == current_processing_phase_num: continue

        future_phase_directives = curriculum_manager.get_processing_directives(future_phase_idx)

        # Deferral decision should primarily use static phase keywords of the target phase

        defer_score, defer_primary_matches = score_text_against_keywords(

            link_anchor_text,

            future_phase_directives.get("phase_keywords_primary", []),

            future_phase_directives.get("phase_keywords_secondary", []),

            future_phase_directives.get("phase_keywords_anti", [])

        )

        min_primary_defer = future_phase_directives.get("phase_min_primary_keyword_matches_for_link_follow", 2)

        min_total_score_defer = future_phase_directives.get("phase_min_total_keyword_score_for_link_follow", 2.0)



        if defer_primary_matches >= min_primary_defer and defer_score >= min_total_score_defer:

            if defer_score > best_future_phase_score:

                best_future_phase_score = defer_score

                best_future_phase_num = future_phase_idx

    

    if best_future_phase_num is not None:

        action = "DEFER_SHALLOW" if current_phase_directives.get("allow_shallow_dive_for_future_phase_links", True) else "DEFER_URL_ONLY"

        return action, best_future_phase_num, best_future_phase_score # Score here is for the *target* future phase

    

    return "IGNORE", None, final_priority_score # Score here is against current phase



def autonomous_learning_cycle():

    initialize_data_files_if_needed()

    logic_node = LogicNode()

    symbolic_node = SymbolicNode()

    curriculum_manager = CurriculumManager()

    dynamic_bridge = DynamicBridge(logic_node, symbolic_node, curriculum_manager)

    print("🚀 Starting Autonomous Learning Cycle...")



    for current_phase_processing_num in range(1, curriculum_manager.get_max_phases() + 1):

        curriculum_manager.current_phase = current_phase_processing_num

        current_phase_directives = curriculum_manager.get_processing_directives(current_phase_processing_num)

        print(f"\n🌀 =====  PHASE {current_phase_processing_num} ({current_phase_directives['info']})  =====")

        

        # MODIFIED: urls_to_scrape_this_phase is now a list of (priority_score, url, anchor_text, hop_count)

        # It will be treated as a priority queue (highest score processed first).

        priority_queue_this_phase = []

        

        # Add seed URLs with a default high priority

        for seed_url in PHASE_URL_SOURCES.get(current_phase_processing_num, []):

            # Score seed URLs against current phase to give them a starting priority

            # Anchor text for seed URLs is often the URL itself or a placeholder

            seed_anchor = Path(seed_url).name # Use filename/last part of URL as pseudo-anchor

            static_score, _ = score_text_against_keywords(seed_anchor, 

                                                          current_phase_directives.get("phase_keywords_primary", []),

                                                          current_phase_directives.get("phase_keywords_secondary", []),

                                                          current_phase_directives.get("phase_keywords_anti", []))

            priority_queue_this_phase.append((static_score if static_score > 0 else 5.0, seed_url, seed_anchor, 0)) # Default high priority for seeds



        # Add deferred URLs with a calculated priority

        for deferred_url in deferred_urls_by_phase.pop(current_phase_processing_num, []):

            # We'd need to have stored anchor text for deferred URLs if we want to score them accurately here

            # For simplicity, give them a moderate priority, or try to fetch title as anchor.

            # This part can be improved by storing anchor text with deferred URLs.

            deferred_anchor = Path(deferred_url).name 

            static_score, _ = score_text_against_keywords(deferred_anchor,

                                                          current_phase_directives.get("phase_keywords_primary", []),

                                                          current_phase_directives.get("phase_keywords_secondary", []),

                                                          current_phase_directives.get("phase_keywords_anti", []))

            priority_queue_this_phase.append((static_score if static_score > 0 else 3.0, deferred_url, deferred_anchor, 0)) # Moderate priority for deferred



        priority_queue_this_phase = list(set(priority_queue_this_phase)) # Remove duplicates (based on all tuple elements)

        priority_queue_this_phase.sort(key=lambda x: x[0], reverse=True) # Sort by priority, highest first



        urls_processed_in_session_count = 0

        max_urls_for_session = current_phase_directives.get("max_urls_to_process_per_phase_session", 2)

        

        # APPENDED: For dynamic session keyword accumulation

        session_hot_keywords = Counter() # Using Counter for frequency

        MAX_HOT_KEYWORDS = current_phase_directives.get("max_session_hot_keywords", 20) # Limit size

        MIN_HOT_KEYWORD_FREQ = current_phase_directives.get("min_session_hot_keyword_freq", 2) # Min times seen to be "hot"



        # --- Main Scraping Loop for the Current Phase ---

        while priority_queue_this_phase and urls_processed_in_session_count < max_urls_for_session:

            # Pop the highest priority URL

            current_priority, current_url, current_anchor, current_hop_count = priority_queue_this_phase.pop(0) 



            if current_url in visited_urls_globally: continue

            if current_hop_count > current_phase_directives.get("max_exploration_depth_from_seed_url", 5):

                print(f"    Max hop depth ({current_hop_count}) reached for {current_url}. Skipping.")

                continue

            

            print(f"\n🔗 Processing URL ({urls_processed_in_session_count + 1}/{max_urls_for_session}, hop {current_hop_count}, prio {current_priority:.2f}): {current_url}")

            visited_urls_globally.add(current_url)

            urls_processed_in_session_count += 1

            curriculum_manager.update_metrics(current_phase_processing_num, urls_visited_increment=1)



            raw_html_content = web_parser.fetch_raw_html(current_url)

            if not raw_html_content: time.sleep(random.uniform(1,2)); continue

            

            extracted_links_with_anchor = web_parser.extract_links_with_text_from_html(current_url, raw_html_content)

            main_page_text = web_parser.clean_html_to_text(raw_html_content)

            if not main_page_text: time.sleep(random.uniform(1,2)); continue



            page_chunks = P_Parser.chunk_content(main_page_text, max_chunk_size=1000)

            print(f"    📄 Page yielded {len(page_chunks)} chunks. Processing...")

            page_had_highly_relevant_chunk = False

            for chunk_num, chunk in enumerate(page_chunks):

                chunk_score_current_phase, prim_matches_current = score_text_against_keywords(

                    chunk, 

                    current_phase_directives.get("phase_keywords_primary", []),

                    current_phase_directives.get("phase_keywords_secondary", []),

                    current_phase_directives.get("phase_keywords_anti", [])

                )

                is_relevant_for_current_processing = (prim_matches_current >= current_phase_directives.get("phase_min_primary_keyword_matches_for_chunk_relevance", 1) and

                                                      chunk_score_current_phase >= current_phase_directives.get("phase_min_total_keyword_score_for_chunk_relevance", 1.0))



                target_storage_phase_for_chunk = dynamic_bridge.determine_target_storage_phase(chunk, current_phase_processing_num)

                

                dynamic_bridge.route_chunk_for_processing(

                    text_input=chunk, source_url=current_url,

                    current_processing_phase=current_phase_processing_num,

                    target_storage_phase=target_storage_phase_for_chunk,

                    is_highly_relevant_for_current_phase=is_relevant_for_current_processing,

                    is_shallow_content=False

                )

                if is_relevant_for_current_processing:

                    page_had_highly_relevant_chunk = True

                    # APPENDED: Accumulate keywords from relevant chunks

                    chunk_kws = P_Parser.extract_keywords(chunk, max_keywords=5) # Get a few top keywords

                    session_hot_keywords.update(chunk_kws)

            

            # Prune session_hot_keywords (keep top N most frequent that meet min frequency)

            if session_hot_keywords:

                 pruned_hot_keywords = Counter({

                     kw: count for kw, count in session_hot_keywords.most_common(MAX_HOT_KEYWORDS * 2) # Take more initially

                     if count >= MIN_HOT_KEYWORD_FREQ

                 })

                 session_hot_keywords = Counter(dict(pruned_hot_keywords.most_common(MAX_HOT_KEYWORDS))) # Then trim to final max

                 # if pruned_hot_keywords: print(f"    🔥 Session Hot Keywords (Top {MAX_HOT_KEYWORDS}): {list(session_hot_keywords.keys())}")





            # --- Evaluate Extracted Links ---

            new_links_to_consider = []

            for link_url_abs, link_anchor_text in extracted_links_with_anchor:

                if link_url_abs in visited_urls_globally or urlparse(link_url_abs).netloc != urlparse(current_url).netloc: continue



                # APPENDED: Pass session_hot_keywords to evaluate_link_action

                action, target_phase_for_link, link_priority_score = evaluate_link_action(

                    link_anchor_text, link_url_abs, current_phase_processing_num, curriculum_manager, session_hot_keywords

                )



                if action == "FOLLOW_NOW":

                    new_links_to_consider.append((link_priority_score, link_url_abs, link_anchor_text, current_hop_count + 1))

                elif action == "DEFER_SHALLOW":

                    if link_url_abs not in deferred_urls_by_phase.get(target_phase_for_link, []):

                        deferred_urls_by_phase[target_phase_for_link].append(link_url_abs)

                        shallow_content = web_parser.fetch_shallow(link_url_abs, max_chars=current_phase_directives["shallow_dive_max_chars"])

                        if shallow_content:

                            dynamic_bridge.route_chunk_for_processing(

                                text_input=shallow_content, source_url=link_url_abs,

                                current_processing_phase=current_phase_processing_num,

                                target_storage_phase=target_phase_for_link,

                                is_highly_relevant_for_current_phase=False,

                                is_shallow_content=True

                            )

                elif action == "DEFER_URL_ONLY":

                    if link_url_abs not in deferred_urls_by_phase.get(target_phase_for_link, []):

                        deferred_urls_by_phase[target_phase_for_link].append(link_url_abs)

            

            # Add newly found relevant links to the main priority queue and re-sort

            if new_links_to_consider:

                print(f"    🔗 Adding {len(new_links_to_consider)} new links to consider for phase queue. Re-prioritizing...")

                for p_score, n_url, n_anchor, n_hop in new_links_to_consider:

                    # Avoid adding if already in queue (check by URL)

                    if not any(item[1] == n_url for item in priority_queue_this_phase):

                        priority_queue_this_phase.append((p_score, n_url, n_anchor, n_hop))

                

                priority_queue_this_phase.sort(key=lambda x: x[0], reverse=True) # Sort by priority, highest first

                # print(f"    Top 3 in queue: {[(item[0], Path(item[1]).name) for item in priority_queue_this_phase[:3]]}")



            time.sleep(random.uniform(0.5, 1.2)) # Polite delay



        # --- End of Scraping Loop for the Current Phase ---

        print(f"🏁 Phase {current_phase_processing_num} scraping session complete. Processed {urls_processed_in_session_count} URLs.")

        print(f"🔬 Running meta-symbol analysis for data up to phase {current_phase_processing_num}...")

        symbolic_node.run_meta_symbol_analysis(max_phase_to_consider=current_phase_processing_num)

        if curriculum_manager.advance_phase_if_ready(current_phase_processing_num):

             print(f"🎉 Advanced to Phase {curriculum_manager.get_current_phase()} based on metrics!")

        else:

            if current_phase_processing_num < curriculum_manager.get_max_phases():

                 print(f"📊 Metrics not yet met to advance from Phase {current_phase_processing_num}.")

            elif current_phase_processing_num == curriculum_manager.get_max_phases():

                 print("🏁 All curriculum phases processed in this learning cycle.")



    print("\n✅ Autonomous Learning Cycle Finished.")

    with open(DATA_DIR / "deferred_urls_log.json", "w", encoding="utf-8") as f:

        json.dump({k: list(set(v)) for k,v in deferred_urls_by_phase.items()}, f, indent=2, ensure_ascii=False)

    print(f"Deferred URLs saved to {DATA_DIR / 'deferred_urls_log.json'}")

    print(f"Total URLs visited globally: {len(visited_urls_globally)}")



if __name__ == "__main__":

    print("Starting autonomous learning process with dynamic link prioritization...")

    try:

        autonomous_learning_cycle()

    except KeyboardInterrupt:

        print("\n🛑 Autonomous learning interrupted by user.")

        with open(DATA_DIR / "deferred_urls_log.json", "w", encoding="utf-8") as f:

            json.dump({k: list(set(v)) for k,v in deferred_urls_by_phase.items()}, f, indent=2, ensure_ascii=False)

        print(f"Deferred URLs saved to {DATA_DIR / 'deferred_urls_log.json'} due to interruption.")

    except Exception as e:

        print(f"💥 An error occurred during autonomous learning: {e}")

        import traceback

        traceback.print_exc()

        with open(DATA_DIR / "deferred_urls_log.json", "w", encoding="utf-8") as f:

            json.dump({k: list(set(v)) for k,v in deferred_urls_by_phase.items()}, f, indent=2, ensure_ascii=False)

        print(f"Deferred URLs saved to {DATA_DIR / 'deferred_urls_log.json'} due to error.")