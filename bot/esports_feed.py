"""esports_feed.py — TheLineShift esports schedule + form feed.

PRIMARY:  PandaScore (CS2 / LoL / Dota 2 / Valorant) — free plan, 1,000 req/hr.
          Token lives in /mnt/agents/work/.pandascore_token or env PANDASCORE_TOKEN.
FALLBACK: OpenDota (Dota 2 results/stats) — keyless, always on.

Output: normalized match candidates with form data for the scan engine.
"""
import json, os, time, urllib.request, urllib.parse

PS_BASE = 'https://api.pandascore.co'
UA = {'User-Agent': 'TheLineShift/1.0 (thelineshift.com)'}
# PandaScore kept legacy slugs: CS2 = 'csgo'
PS_GAMES = {'cs2': 'csgo', 'lol': 'lol', 'dota2': 'dota2', 'valorant': 'valorant'}


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def token():
    for p in ('/mnt/agents/work/.pandascore_token',
              os.path.join(os.path.dirname(os.path.abspath(__file__)), '.pandascore_token')):
        if os.path.exists(p):
            return open(p).read().strip()
    return os.environ.get('PANDASCORE_TOKEN')


def ps_get(path, **params):
    tk = token()
    if not tk:
        raise RuntimeError('no pandascore token')
    params['token'] = tk
    return _get(f'{PS_BASE}{path}?{urllib.parse.urlencode(params)}')


def _opp(m, i):
    try:
        o = m['opponents'][i]['opponent']
        return {'id': o.get('id'), 'name': o.get('name', '?')}
    except Exception:
        return {'id': None, 'name': 'TBD'}


def ps_upcoming(game_key, pages=1):
    """Upcoming matches for one title, normalized."""
    out = []
    slug = PS_GAMES[game_key]
    for pg in range(1, pages + 1):
        try:
            ms = ps_get(f'/{slug}/matches/upcoming', per_page=20, page=pg)
        except Exception as e:
            print(f'ps_upcoming {game_key} p{pg}: {e}')
            break
        for m in ms or []:
            out.append({
                'game': game_key, 'match_id': m.get('id'),
                'team_a': _opp(m, 0), 'team_b': _opp(m, 1),
                'start_utc': m.get('begin_at') or m.get('scheduled_at'),
                'tournament': (m.get('tournament') or {}).get('name', ''),
                'league': (m.get('league') or {}).get('name', ''),
                'best_of': m.get('number_of_games'),
                'source': 'pandascore'})
        if not ms or len(ms) < 20:
            break
        time.sleep(0.3)
    return out


def ps_team_form(team_id, game_key, n=5):
    """Last-n series record (W-L) for a team, from finished matches."""
    try:
        slug = PS_GAMES[game_key]
        ms = ps_get(f'/{slug}/matches/past', **{'filter[opponent_id]': team_id, 'per_page': n})
        w = l = 0
        for m in ms or []:
            winner = (m.get('winner') or {}).get('id')
            if winner is None:
                continue
            if winner == team_id:
                w += 1
            else:
                l += 1
        return {'w': w, 'l': l}
    except Exception as e:
        print(f'ps_team_form {team_id}: {e}')
        return None


def opendota_pro_form(limit=100):
    """Keyless Dota 2 pro results -> per-team series form over recent matches."""
    ms = _get(f'https://api.opendota.com/api/proMatches')
    form = {}
    for m in ms[:limit]:
        for side, key in (('radiant', 'radiant_name'), ('dire', 'dire_name')):
            name = m.get(key)
            if not name:
                continue
            f = form.setdefault(name, {'w': 0, 'l': 0})
            won = (side == 'radiant') == bool(m.get('radiant_win'))
            f['w' if won else 'l'] += 1
    return form


def candidates(games=('cs2', 'lol', 'dota2', 'valorant'), with_form=True):
    """All upcoming matches across titles, form-attached when possible."""
    out = []
    for g in games:
        try:
            out += ps_upcoming(g)
        except Exception as e:
            print(f'candidates {g}: {e}')
    if with_form:
        for c in out:
            for side in ('team_a', 'team_b'):
                t = c[side]
                if t.get('id'):
                    t['form'] = ps_team_form(t['id'], c['game'])
                    time.sleep(0.3)
    return out


def rank_plays(cands):
    """Score each match: favorite by recent form -> unit-weighted ML candidate."""
    plays = []
    for c in cands:
        fa, fb = c['team_a'].get('form'), c['team_b'].get('form')
        if not fa or not fb or (fa['w'] + fa['l'] < 3) or (fb['w'] + fb['l'] < 3):
            continue
        wa = fa['w'] / max(1, fa['w'] + fa['l'])
        wb = fb['w'] / max(1, fb['w'] + fb['l'])
        edge = wa - wb
        if abs(edge) < 0.2:
            continue
        fav, dog = (c['team_a'], c['team_b']) if edge > 0 else (c['team_b'], c['team_a'])
        units = 3 if abs(edge) >= 0.5 else (2 if abs(edge) >= 0.35 else 1)
        plays.append({**c, 'pick': fav['name'], 'vs': dog['name'],
                      'edge': round(abs(edge), 2), 'units': units,
                      'market': f"ML (Bo{c.get('best_of') or '?'})"})
    return sorted(plays, key=lambda p: -p['edge'])


if __name__ == '__main__':
    if token():
        cs = candidates()
        print(f'{len(cs)} upcoming matches')
        for p in rank_plays(cs)[:10]:
            print(f"  [{p['game']}] {p['pick']} over {p['vs']} — {p['units']}u edge {p['edge']} ({p['tournament']})")
    else:
        print('no PandaScore token — OpenDota form sample:')
        f = opendota_pro_form(60)
        top = sorted(f.items(), key=lambda kv: -kv[1]['w'])[:8]
        for name, r in top:
            print(f"  {name}: {r['w']}-{r['l']}")
