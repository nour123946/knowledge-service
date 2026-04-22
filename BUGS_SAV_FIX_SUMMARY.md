# Correction des Bugs SAV - Synthèse

## Bug 1: Échange taille - Retrouver tailles dans history
**Problème:** 
- Après que l'utilisateur a donné "taille reçue 38 taille souhaitée 39", le bot demande si l'article est neuf
- Quand l'utilisateur répond "oui", le bot redemandait les tailles au lieu de finaliser

**Cause:** 
- `build_sav_reply(exchange_return)` ne mémorisait pas sr/sw dans history
- `extract_exchange_details("oui")` retournait sr/sw = None

**Solution (Option B: History Robuste):**

### 1. [app/core/sav.py] - Nouvelle helper function
```python
def _find_exchange_details_in_history(history: Optional[List[Dict[str, Any]]], limit_messages: int = 10) -> Dict[str, Any]:
    """Parcourt l'historique du plus récent au plus ancien pour retrouver tailles."""
```
- Scanne les 10 derniers messages user dans history
- Retrouve la dernière paire de tailles (sr/sw)
- Retourne {size_received, size_wanted}

### 2. [app/core/sav.py] - Modification de build_sav_reply
**Avant:**
```python
def build_sav_reply(category, last_order, user_text, last_bot_text="") -> str:
```

**Après:**
```python
def build_sav_reply(category, last_order, user_text, last_bot_text="", 
                    history: Optional[List[Dict[str, Any]]] = None) -> str:
```

### 3. [app/core/sav.py] - Logic for exchange_return
Ajouté avant les vérifications de tailles:
```python
# BUG 1 FIX: Si condition donnée (oui/non) mais tailles manquantes dans user_text
if is_new is not None and (not sr or not sw):
    hist_sizes = _find_exchange_details_in_history(history)
    if hist_sizes["size_received"] and hist_sizes["size_wanted"]:
        # Retrouvé dans l'historique!
        sr = hist_sizes["size_received"]
        sw = hist_sizes["size_wanted"]
```

### 4. [app/main.py] - Passage de history à build_sav_reply
Modifié aux 3 endroits où `build_sav_reply()` est appelée (line 437, 671, 1019):
```python
# AVANT:
sav_answer = build_sav_reply(category=cat, last_order=last_order, 
                             user_text=query, last_bot_text="")

# APRÈS:
history_for_sav = get_history(session_id, last_n=8)
sav_answer = build_sav_reply(category=cat, last_order=last_order, 
                             user_text=query, last_bot_text="", 
                             history=history_for_sav)
```

**Résultat:** 
- User: "taille reçue 38 taille souhaitée 39" -> stocké dans history
- Bot: "Article neuf?" 
- User: "oui" -> bot retrouve tailles dans history
- Bot: "Je transmets..." ✓ (Plus de redémarrage!)

---

## Bug 2: Switch intention en plein flow SAV
**Problème:**
- User en état sav_exchange_return, bot demande tailles
- User dit "je veux changer l'adresse de livraison"
- Bot continue à demander tailles (mauvais)

**Solution:** Détecter changement de catégorie SAV et switcher

### 1. [app/main.py] - Nouveau handler SAV actif (ligne 264-319)
Ajouté après la définition de `is_in_sav_flow`:
```python
if is_in_sav_flow and (not state == "sav_waiting_category") and len((query or "").strip()) > 4:
    # Éviter de switcher sur de simples confirmations (oui/non)
    q_lower = (query or "").lower().strip()
    is_simple_confirmation = q_lower in {oui, non, ok, ...}
    
    if not is_simple_confirmation:
        # Chercher si autre catégorie SAV explicitement mentionnée
        new_sav_cat = detect_sav_category(query, last_bot_msg)
        current_cat = state.replace("sav_", "")
        
        # Si catégorie DIFFÉRENTE => SWITCH
        if new_sav_cat and new_sav_cat != current_cat:
            # Appeler build_sav_reply avec new_cat et history
            switch_answer = build_sav_reply(
                category=new_sav_cat,
                last_order=last_order,
                user_text=query,
                last_bot_text=last_bot_msg,
                history=history_for_switch
            )
            # Return avec new conversation_state=f"sav_{new_sav_cat}"
```

**Logique de détection:**
1. ✓ Être en SAV flow actif (state="sav_exchange_return" par exemple)
2. ✓ Message > 4 caractères (pas juste "oui"/"non")
3. ✓ Pas une simple confirmation (liste des oui/non/ok/1-5)
4. ✓ detect_sav_category() détecte nouvelle catégorie
5. ✓ Nouvelle catégorie ≠ Catégorie courante
6. → SWITCH vers sav_{new_categorie}

**Résultat:**
```
État: sav_exchange_return (bot demande tailles)
User: "je veux changer l'adresse de livraison"
→ Détecte: delivery_issue
→ Switch: sav_exchange_return → sav_delivery_issue
→ Demande adresse: "Confirmez votre adresse? (Oui/Non)"
```

---

## Tests de validation

### Test Bug 1: ✓ PASSED
```
Step 1: Bot demande tailles
Step 2: User: "taille reçue 38 taille souhaitée 39" (sauvegardé en history)
Step 3: User: "oui"
  → Bot retrouve tailles 38/39 dans history
  → Bot finalise: "Je transmets..." ✓
```

### Test Bug 2: ✓ PASSED
```
Test 1: classify_sav_category("je veux changer l'adresse", state="sav_exchange_return")
  → Result: delivery_issue (conf 0.90) ✓
  
Test 2: User "oui" dans sav_exchange_return
  → Length=3 <= 4, skip switch check (confirmation mode) ✓
  
Test 3: User "je veux changer l'adresse" dans sav_exchange_return
  → Length=38 > 4
  → Not simple confirmation
  → Détecte: delivery_issue
  → Different from exchange_return → SWITCH! ✓
```

---

## Fichiers modifiés

1. **app/core/sav.py**
   - Ajouté: `_find_exchange_details_in_history()` (ligne 199-215)
   - Modifié: `build_sav_reply()` signature (ligne 225)
   - Modifié: exchange_return logic (ligne 239-253)

2. **app/main.py**
   - Ajouté: SAV switch handler (ligne 264-319)
   - Modifié: 3 appels à `build_sav_reply()` pour passer history (ligne 437, 671, 1019)

---

## Robustesse

✓ **History limit:** max 10 messages cherchés (évite boucles infinies)
✓ **Confirmation protection:** n'active pas le switch sur "oui/non/ok/1-5"
✓ **Default values:** history=None par défaut, compatible ancien code
✓ **Graceful fallback:** Si pas taille en history, redemande tailles normalement
✓ **No breaking changes:** Tous les appels existants toujours compatibles
