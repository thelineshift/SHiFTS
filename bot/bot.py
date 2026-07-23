import os, json, time, base64, asyncio, urllib.request, random, re, datetime
import discord
from discord.ext import tasks

DISCORD_TOKEN = os.environ['DISCORD_BOT_TOKEN']
GH_TOKEN = os.environ.get('GITHUB_TOKEN', '')
REPO = 'TheLineShift/AISportsBot'
QUEUE_BRANCH = 'commands'
RAW = f'https://raw.githubusercontent.com/{REPO}/{QUEUE_BRANCH}'
API = f'https://api.github.com/repos/{REPO}/contents'

TIER_ROLES = {'\U0001F512 Lock Room': 'lock', '\U0001F4CA Sharp': 'sharp', '\U0001F40B Whale': 'whale'}

BOT_NICK = '🤖 SHiFT'
BOT_STATUS = 'the board 🛰️'

SCAM_RX = [r'\bd[\.\s]*m[\.\s]*me\b', r'send (me )?a d[\.\s]*m', r'\bdm for\b', r'direct message me',
           r't\.me/', r'telegram', r'whats?app', r'free nitro', r'nitro for free', r'claim (your|ur)',
           r'airdrop', r'double your', r'forex', r'investment platform', r'guaranteed profit',
           r'trading (expert|guru|signals)', r'contact (me|admin) (on|via)']
OUR_INVITE = '8bBxWUJCYT'

async def shift_guard(message, guild):
    try:
        member = message.author
        if getattr(member, 'bot', False):
            return False
        try:
            if member == guild.owner or member.guild_permissions.administrator or member.guild_permissions.manage_guild:
                return False
        except Exception:
            pass
        content = message.content or ''
        low = content.lower()
        reason = None
        if message.mention_everyone:
            reason = '@everyone/@here ping by non-staff'
        else:
            for pat in SCAM_RX:
                if re.search(pat, low):
                    reason = 'scam pattern'
                    break
            if not reason:
                invites = re.findall(r'(?:discord\.gg/|discord\.com/invite/)([A-Za-z0-9]+)', content)
                if any(code != OUR_INVITE for code in invites):
                    reason = 'foreign discord invite'
            if not reason and len(message.mentions) >= 4:
                reason = f'mass mentions ({len(message.mentions)})'
        if not reason:
            return False
        st = await asyncio.to_thread(get_state)
        offs = st.setdefault('mod_offenses', {})
        uid = str(member.id)
        offs[uid] = offs.get(uid, 0) + 1
        await asyncio.to_thread(gh_put, 'bot_state.json', st, 'mod offense ' + uid)
        snippet = content[:180]
        try:
            await message.delete()
        except Exception:
            pass
        action = 'deleted'
        try:
            import datetime as _dt
            await member.timeout(_dt.timedelta(minutes=60), reason='SHiFT guard: ' + reason)
            action += ' + 60min timeout'
        except Exception:
            pass
        if offs[uid] >= 2:
            try:
                await member.kick(reason='repeat scam offenses')
                action += ' + KICKED (repeat)'
            except Exception:
                pass
        lab = find_channel(guild, 'shift-lab')
        if lab:
            await lab.send(f"\U0001F6E1\uFE0F **SHiFT GUARD** — {action}\n\U0001F464 {member} (`{member.id}`) in #{message.channel.name}\n\u2696\uFE0F {reason}\n\U0001F4DD {snippet or '(no text)'}")
        await asyncio.to_thread(log_event, 'guard_action', f'{action} {member} in #{message.channel.name}: {reason}')
        return True
    except Exception as e:
        print('shift_guard error:', e)
        return False

def log_event(type_, detail):
    """Append an event to the ops event log (dashboard Event Log feed)."""
    try:
        ev = gh_get_json('events.json') or {'events': [], 'next_id': 1}
        ev.setdefault('events', []).append({'id': ev.get('next_id', 1), 'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                                            'type': type_, 'detail': str(detail)[:300]})
        ev['next_id'] = ev.get('next_id', 1) + 1
        ev['events'] = ev['events'][-250:]
        gh_put('events.json', ev, 'event: ' + type_)
    except Exception as e:
        print('[event] log failed:', e)


ISSUE_RX_PAYMENT = re.compile(r'(charg|payment|paid|refund|whop|stripe|billing|invoice|card|subscri)', re.I)
ISSUE_RX_ROLE = re.compile(r"(role|tier|access|can't see|cannot see|locked out|missing.*(channel|room)|upgrade|downgrade)", re.I)
ISSUE_RX_DATA = re.compile(r'(wrong|incorrect|error|typo|bug|broken|picture|image|photo|weather|matchup|odds|line|score|missing pick)', re.I)
ISSUE_RX_YES = re.compile(r'^\s*(yes|yeah|yep|yup|fixed|works|working now|all good|confirmed|it works)\b', re.I)
ISSUE_RX_NO = re.compile(r"^\s*(no|nope|not fixed|still|doesn't work|didn't work|not working)\b", re.I)


async def handle_issue(message, guild):
    """issues channel: triage, auto-fix what is fixable, verify with the user, escalate the rest."""
    chname = getattr(message.channel, 'name', '') or ''
    author = message.author
    if 'issues' not in chname or author.bot:
        return
    content = (message.content or '').strip()
    if not content:
        return
    issues = gh_get_json('issues.json') or {'tickets': [], 'next_id': 1}
    tickets = issues.setdefault('tickets', [])
    t = next((x for x in reversed(tickets) if x.get('user_id') == str(author.id)
              and x.get('status') in ('open', 'awaiting_user')), None)
    reply = None
    if t and t.get('status') == 'awaiting_user':
        if ISSUE_RX_YES.search(content):
            t['status'] = 'resolved'; t['resolved_ts'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            reply = f'✅ Ticket **#{t["id"]}** marked resolved. Thanks for confirming, {author.mention}!'
            asyncio.ensure_future(asyncio.to_thread(log_event, 'issue_resolved', f'ticket #{t["id"]} ({author}) self-confirmed fixed'))
        elif ISSUE_RX_NO.search(content):
            t['status'] = 'escalated'; t['escalated_ts'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            reply = (f"📨 Got it — I've **escalated ticket #{t['id']} to the admin**. "
                     f"We'll get back to you as soon as an admin is available to fix your issue.")
            asyncio.ensure_future(asyncio.to_thread(log_event, 'issue_escalated', f'ticket #{t["id"]} ({author}): fix not confirmed'))
            lab = find_channel(guild, 'shift-lab')
            if lab:
                asyncio.ensure_future(lab.send(f'🚨 ESCALATED ticket #{t["id"]} — {author} ({author.id}): {t.get("summary","")[:200]} — auto-fix failed, needs admin.'))
        else:
            reply = f'🤖 Ticket **#{t["id"]}** is waiting on your confirmation — did the fix work? Reply **yes** or **no**.'
    elif not t:
        tid = issues.get('next_id', 1); issues['next_id'] = tid + 1
        t = {'id': tid, 'user_id': str(author.id), 'user': str(author), 'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
             'status': 'open', 'summary': content[:250]}
        tickets.append(t)
        kind = ('payment' if ISSUE_RX_PAYMENT.search(content) else
                'access' if ISSUE_RX_ROLE.search(content) else
                'data' if ISSUE_RX_DATA.search(content) else 'other')
        t['kind'] = kind
        lab = find_channel(guild, 'shift-lab')
        if kind == 'payment':
            t['status'] = 'escalated'; t['escalated_ts'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            reply = (f"🎫 Ticket **#{tid}** opened (billing). Payment issues need the admin — I've **escalated this to the admin** "
                     f"and we'll get back to you as soon as an admin is available to fix your issue.")
            asyncio.ensure_future(asyncio.to_thread(log_event, 'issue_escalated', f'ticket #{tid} payment ({author})'))
            if lab:
                asyncio.ensure_future(lab.send(f'🚨 PAYMENT ticket #{tid} — {author} ({author.id}): {content[:300]}'))
        elif kind == 'access':
            fixed = False
            try:
                member = guild.get_member(author.id)
                if member:
                    names = [r.name for r in member.roles]
                    want = []
                    if any('whale' in n.lower() for n in names):
                        want = ['Sharp', 'Lock']
                    elif any('sharp' in n.lower() for n in names):
                        want = ['Lock']
                    for word in want:
                        for r in guild.roles:
                            if word.lower() in r.name.lower() and r not in member.roles:
                                asyncio.ensure_future(member.add_roles(r, reason=f'issues ticket #{tid} hierarchy fix'))
                                fixed = True
            except Exception as e:
                print('[issues] role fix failed:', e)
            t['status'] = 'awaiting_user'
            reply = (f"🎫 Ticket **#{tid}** opened (access). I've checked your tier roles and restored the room access your tier includes. "
                     f"**Can you confirm it's fixed?** Reply **yes** or **no** — if it's still broken I'll escalate this to the admin immediately.")
            asyncio.ensure_future(asyncio.to_thread(log_event, 'issue_opened', f'ticket #{tid} access ({author}) auto-fix={fixed}'))
        elif kind == 'data':
            t['status'] = 'open'
            reply = (f"🎫 Ticket **#{tid}** opened. Thanks for flagging it — I'm reviewing the data/post now and will correct anything "
                     f"that's wrong. I'll follow up here shortly.")
            asyncio.ensure_future(asyncio.to_thread(log_event, 'issue_opened', f'ticket #{tid} data ({author}): {content[:150]}'))
            if lab:
                asyncio.ensure_future(lab.send(f'🛠️ DATA ticket #{tid} — {author}: {content[:300]}'))
        else:
            t['status'] = 'open'
            reply = (f"🎫 Ticket **#{tid}** opened. Can you give me a bit more detail (what you expected vs what you're seeing)? "
                     f"If I can't fix it myself I'll escalate it to the admin right away.")
            asyncio.ensure_future(asyncio.to_thread(log_event, 'issue_opened', f'ticket #{tid} other ({author}): {content[:150]}'))
    else:
        t['summary'] = (t.get('summary', '') + ' | ' + content)[:250]
        reply = f'🤖 Added that to ticket **#{t["id"]}** — still on it.'
    try:
        gh_put('issues.json', issues, f'issue ticket update ({author.id})')
    except Exception as e:
        print('[issues] state write failed:', e)
    if reply:
        await message.reply(reply, mention_author=False)



def make_client(privileged=True):
    intents = discord.Intents.default()
    intents.guilds = True
    intents.members = privileged
    intents.message_content = privileged
    c = discord.Client(intents=intents)

    @c.event
    async def on_ready():
        print(f'LineShift Bot v8.8 online as {c.user} in {len(c.guilds)} guild(s) | privileged={privileged}')
        try:
            await c.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=BOT_STATUS))
            g0 = c.guilds[0] if c.guilds else None
            if g0 and g0.me and g0.me.nick != BOT_NICK:
                await g0.me.edit(nick=BOT_NICK)
                print('nick applied:', BOT_NICK)
        except Exception as e:
            print('presence/nick error:', e)
        if not poll.is_running():
            poll.start()
        if not countdown.is_running():
            countdown.start()
        if not audit.is_running():
            audit.start()
        if not grader.is_running():
            grader.start()
        if not x_drainer.is_running():
            x_drainer.start()
        if not scan_event_watch.is_running():
            scan_event_watch.start()
        if not recap_watch.is_running():
            recap_watch.start()
        if not teaser_watch.is_running():
            teaser_watch.start()
        if not odds_watch.is_running():
            odds_watch.start()

    @c.event
    async def on_message(message):
        try:
            if message.author.bot:
                return
            if message.guild and await shift_guard(message, message.guild):
                return
            chname = (getattr(message.channel, 'name', '') or '').lower()
            if 'giveaway' in chname:
                raw = message.content or ''
                hs = list(re.findall(r'@([A-Za-z0-9_]{4,15})\b', raw))
                hs += re.findall(r'(?:https?://)?(?:www\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{4,15})', raw, flags=re.I)
                hs = [h for h in hs if h.lower() not in ('thelineshift', 'everyone', 'here', 'status', 'home', 'search', 'explore', 'i')]
                if hs:
                    await verify_giveaway_entry(message, hs[0])
                return
            if 'issues' in chname:
                await handle_issue(message, message.guild or (c.guilds[0] if c.guilds else None))
                return
            content = (message.content or '').strip().strip('`').strip('<>')
            if 'thelineshift.com' not in content or 'code=' not in content:
                return
            url = content.split()[0]
            guild = message.guild or (c.guilds[0] if c.guilds else None)
            log = []
            try:
                await message.delete()
            except Exception:
                pass
            await run_command({'action': 'x_link_finish', 'url': url}, guild, log)
            ok = any('OK' in l for l in log)
            await message.channel.send('✅ X link complete — native posting is LIVE. First post fired.' if ok
                                       else '❌ Exchange failed: ' + ' | '.join(log)[-300:])
        except Exception as e:
            print('on_message x-link error:', e)

    @c.event
    async def on_raw_reaction_add(payload):
        try:
            state = await asyncio.to_thread(get_state)
            if payload.message_id != state.get('scan_role_msg'):
                return
            guild = c.guilds[0] if c.guilds else None
            role = guild.get_role(state.get('scan_role_id', 0)) if guild else None
            if role and payload.member and not payload.member.bot:
                await payload.member.add_roles(role, reason='scan alert opt-in')
        except Exception as e:
            print('reaction add error:', e)

    @c.event
    async def on_raw_reaction_remove(payload):
        try:
            state = await asyncio.to_thread(get_state)
            if payload.message_id != state.get('scan_role_msg'):
                return
            guild = c.guilds[0] if c.guilds else None
            role = guild.get_role(state.get('scan_role_id', 0)) if guild else None
            member = guild.get_member(payload.user_id) if guild else None
            if role and member and not member.bot:
                await member.remove_roles(role, reason='scan alert opt-out')
        except Exception as e:
            print('reaction remove error:', e)

    @c.event
    async def on_member_update(before, after):
        try:
            added = [r for r in after.roles if r not in before.roles]
            hits = [TIER_ROLES[r.name] for r in added if r.name in TIER_ROLES]
            if not hits:
                return
            links = await asyncio.to_thread(gh_get_json, 'member_links.json')
            entry = links.setdefault(str(after.id), {'name': after.name, 'grants': []})
            entry.setdefault('grants', [])
            entry['name'] = after.name
            def _parse_ts(s):
                try:
                    return datetime.datetime.strptime(s, '%Y-%m-%d %H:%M UTC').replace(tzinfo=datetime.timezone.utc).timestamp()
                except Exception:
                    return 0
            # dedup: ignore re-fired member_update events for a tier already recorded in the last 6h
            hits = [h for h in hits if not any(
                g.get('tier') == h and time.time() - _parse_ts(g.get('at', '')) < 6 * 3600
                for g in entry['grants'])]
            if not hits:
                return
            for h in hits:
                entry['grants'].append({'tier': h, 'at': time.strftime('%Y-%m-%d %H:%M UTC')})
            await asyncio.to_thread(gh_put, 'member_links.json', links, f'tier grant: {after.name} -> {hits[-1]}')
            print(f'TIER GRANT recorded: {after.name} -> {hits}')
        except Exception as e:
            print('on_member_update error:', e)
    return c

def gh_headers():
    return {'Authorization': f'token {GH_TOKEN}', 'User-Agent': 'lineshift-bot'}

def gh_get(path, ref='main'):
    req = urllib.request.Request(f'{API}/{path}?ref={ref}', headers=gh_headers())
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)

def gh_put(path, obj, message, ref=QUEUE_BRANCH):
    try:
        remote = gh_get(path, ref=ref)
        sha = remote.get('sha')
        if path == 'bot_state.json':
            try:
                base = json.loads(base64.b64decode(remote['content']).decode())
                # deep-merge append-only dicts so concurrent writers can't clobber entries (Stella wipe 7/23)
                for dk in ('giveaway_confirmed', 'scan_events'):
                    if isinstance(base.get(dk), dict) and isinstance(obj.get(dk), dict):
                        m = {**base[dk], **obj[dk]}
                        base[dk] = m
                        obj = {**obj, dk: m}
                base.update(obj)
                obj = base
            except Exception:
                pass
    except Exception:
        sha = None
    body = {'message': message, 'branch': ref,
            'content': base64.b64encode(json.dumps(obj, indent=2).encode()).decode()}
    if sha:
        body['sha'] = sha
    req = urllib.request.Request(f'{API}/{path}', data=json.dumps(body).encode(),
                                 method='PUT', headers={**gh_headers(), 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)

def fetch_commands():
    try:
        req = urllib.request.Request(f'{RAW}/bot_commands.json?t={int(time.time())}',
                                     headers={'User-Agent': 'lineshift-bot'})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except Exception:
        return None

def get_state():
    try:
        d = gh_get('bot_state.json', ref=QUEUE_BRANCH)
        return json.loads(base64.b64decode(d['content']))
    except Exception:
        return None

def gh_get_json(path):
    try:
        d = gh_get(path, ref=QUEUE_BRANCH)
        return json.loads(base64.b64decode(d['content']))
    except Exception:
        return {}

def gh_get_json_ref(path, ref):
    try:
        d = gh_get(path, ref=ref)
        return json.loads(base64.b64decode(d['content']))
    except Exception:
        return {}

def pick_game_utc(date_s, time_s):
    # epoch (UTC) of a pick's game time; ET assumed, EDT = UTC-4
    try:
        t = (time_s or '11:00 PM').upper().replace('ET', '').strip()
        m = re.match(r'(\d{1,2})(?::(\d{2}))?\s*(AM|PM)', t)
        if not m:
            return None
        hh = int(m.group(1)) % 12
        if m.group(3) == 'PM':
            hh += 12
        mm = int(m.group(2) or 0)
        y, mo, dd = map(int, str(date_s).split('-'))
        dt = datetime.datetime(y, mo, dd, hh, mm) + datetime.timedelta(hours=4)
        return dt.replace(tzinfo=datetime.timezone.utc).timestamp()
    except Exception:
        return None

def find_channel(guild, name):
    n = name.lower().strip('#').replace(' ', '')
    for ch in guild.text_channels:
        cn = ch.name.lower().replace('-', '').replace('_', '')
        if n.replace('-', '').replace('_', '') in cn:
            return ch
    return None

def find_role(guild, name):
    n = name.lower()
    for r in guild.roles:
        if n in r.name.lower():
            return r
    return None

async def resolve_member(guild, ident):
    ident = str(ident).strip()
    if ident.isdigit():
        try:
            return await guild.fetch_member(int(ident))
        except Exception:
            return None
    try:
        async for m in guild.fetch_members(limit=None):
            if m.name.lower() == ident.lower():
                return m
    except Exception:
        pass
    async for e in guild.audit_logs(limit=100):
        t = getattr(e, 'target', None)
        name = getattr(t, 'name', '') if t is not None else ''
        if name.lower() == ident.lower():
            try:
                return await guild.fetch_member(t.id)
            except Exception:
                return None
    for ch in guild.text_channels:
        try:
            async for msg in ch.history(limit=200):
                if msg.author.name.lower() == ident.lower():
                    try:
                        return await guild.fetch_member(msg.author.id)
                    except Exception:
                        return None
        except Exception:
            pass
    return None


def gh_raw_bytes(path, ref='main'):
    req = urllib.request.Request(f'https://raw.githubusercontent.com/{REPO}/{ref}/{path}?t={int(time.time())}',
                                 headers={'User-Agent': 'lineshift-bot'})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read()

def x_oauth1_sign(method, url, c):
    import hmac as _h, hashlib as _hl, secrets as _sc
    from urllib.parse import quote as _qq
    op = {'oauth_consumer_key': c['api_key'], 'oauth_nonce': _sc.token_hex(16),
          'oauth_signature_method': 'HMAC-SHA1', 'oauth_timestamp': str(int(time.time())),
          'oauth_token': c['access_token'], 'oauth_version': '1.0'}
    q = lambda s: _qq(str(s), safe='')
    base = '&'.join([method, q(url), q('&'.join(f'{q(k)}={q(v)}' for k, v in sorted(op.items())))])
    key = f"{q(c['api_secret'])}&{q(c['access_token_secret'])}"
    op['oauth_signature'] = base64.b64encode(_h.new(key.encode(), base.encode(), _hl.sha1).digest()).decode()
    return 'OAuth ' + ', '.join(f'{k}="{q(v)}"' for k, v in sorted(op.items()))

def x_upload_media(img_bytes, mime='image/png', c=None):
    # v2 upload with OAuth2 user context (media.write); falls back to legacy OAuth1 attempt
    if c is None:
        c = x_creds_load()
    url = 'https://api.x.com/2/media/upload'
    boundary = 'lineshift' + str(int(time.time()))
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="media"; filename="card.png"\r\n'
            f'Content-Type: {mime}\r\n\r\n').encode() + img_bytes + f'\r\n--{boundary}--\r\n'.encode()
    req = urllib.request.Request(url, data=body, method='POST',
                                 headers={'Authorization': f"Bearer {c['oauth2_access']}",
                                          'Content-Type': f'multipart/form-data; boundary={boundary}'})
    with urllib.request.urlopen(req, timeout=40) as r:
        d = json.load(r)
    return str(d.get('data', {}).get('id') or d.get('media_id_string') or d.get('media_id'))

def x_get_json(url, bearer):
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {bearer}'})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)

GW_CACHE = {}
def gw_user_set(url, bearer):
    ts, ids = GW_CACHE.get(url, (0, set()))
    if time.time() - ts < 240:
        return ids
    ids, tok, pages = set(), None, 0
    while pages < 3:
        u = url + ('&pagination_token=' + tok if tok else '')
        d = x_get_json(u, bearer)
        ids |= {str(x.get('id')) for x in d.get('data', [])}
        tok = d.get('meta', {}).get('next_token')
        pages += 1
        if not tok:
            break
    GW_CACHE[url] = (time.time(), ids)
    return ids

def gw_followed(uid, uat):
    try:
        ids = gw_user_set('https://api.x.com/2/users/1831457082828021760/followers?max_results=100', uat)
        return str(uid) in ids
    except Exception:
        return None


TIER_ROOM = {'lock': '🔒 Lock Room', 'sharp': '📊 Sharp Room', 'whale': '🐋 Whale Room', 'free': '🆓 Free'}
def entry_checklist(handle, followed, liked, reposted):
    def line(ok, label):
        return f"{'✅' if ok else '❌'} {label}"
    lines = [line(followed, 'Follow @TheLineShift'),
             line(liked, 'Like the giveaway post'),
             line(reposted, 'Repost the giveaway post')]
    missing = [l for ok, l in zip((followed, liked, reposted),
                                  ('follow @TheLineShift', 'like the giveaway post', 'repost the giveaway post')) if not ok]
    return lines, missing

async def verify_giveaway_entry(message, handle):
    try:
        c = await asyncio.to_thread(x_creds_load)
        bt = c['bearer_token']
        state = await asyncio.to_thread(get_state)
        post_id = (state or {}).get('giveaway_x_post', '2080027230839931367')
        our_id = '1831457082828021760'
        try:
            u = await asyncio.to_thread(x_get_json, f'https://api.x.com/2/users/by/username/{handle}', bt)
            uid = str(u.get('data', {}).get('id') or '')
        except Exception:
            uid = ''
        if not uid:
            await message.channel.send(f"🤖 SHiFT entry check: can't find an X account **@{handle}** — double-check the spelling and drop it again.")
            return
        if time.time() > c.get('oauth2_expires_at', 0):
            c = await asyncio.to_thread(x_oauth2_refresh, c)
        uat = c.get('oauth2_access', bt)
        followed = await asyncio.to_thread(gw_followed, uid, uat)
        try:
            liked = uid in await asyncio.to_thread(gw_user_set, f'https://api.x.com/2/tweets/{post_id}/liking_users?max_results=100', uat)
        except Exception:
            liked = None
        try:
            reposted = uid in await asyncio.to_thread(gw_user_set, f'https://api.x.com/2/tweets/{post_id}/retweeted_by?max_results=100', uat)
        except Exception:
            reposted = None
        def ic(ok, label):
            return f"{'✅' if ok else ('❌' if ok is False else '❓')} {label}"
        checklist = "\n".join([ic(followed, 'Follow @TheLineShift'), ic(liked, 'Like the giveaway post'), ic(reposted, 'Repost the giveaway post')])
        missing = []
        if followed is False: missing.append('follow @TheLineShift')
        if liked is False: missing.append('like the giveaway post')
        if reposted is False: missing.append('repost the giveaway post')
        if not missing and followed and liked and reposted:
            conf = await asyncio.to_thread(gh_get_json_ref, 'giveaway_confirmed.json', QUEUE_BRANCH)
            if handle.lower() in (conf or {}):
                await message.channel.send(f"🎫 **@{handle}** — you're already locked in the pool. Sit tight for Sunday 6 PM ET. 🤖")
                return
            names = [r.name for r in getattr(message.author, 'roles', [])]
            tkey = 'whale' if any('Whale' in n or '🐋' in n for n in names) else 'sharp' if any('Sharp' in n or '📊' in n for n in names) else 'lock' if any('Lock' in n or '🔒' in n for n in names) else 'free'
            mult = {'whale': 5, 'sharp': 3, 'lock': 2, 'free': 1}[tkey]
            conf = await asyncio.to_thread(gh_get_json_ref, 'giveaway_confirmed.json', QUEUE_BRANCH)
            conf = conf or {}
            conf[handle.lower()] = {
                'handle': handle, 'discord': str(message.author), 'discord_id': str(message.author.id),
                'mult': mult, 'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
            await asyncio.to_thread(gh_put, 'giveaway_confirmed.json', conf, 'giveaway confirm ' + handle, QUEUE_BRANCH)
            try:
                await message.add_reaction('✅')
            except Exception:
                pass
            await message.channel.send(
                f"🎫 **ENTRY CONFIRMED — @{handle}**\n\n{checklist}\n🎟️ **Tickets: {mult}x — {TIER_ROOM.get(tkey, tkey)}**\n\nDraw: Sunday 6 PM ET — provably fair, paid on-chain. 🤖")
        else:
            steps = (f"**{len(missing)} step{'s' if len(missing) > 1 else ''} left:** " + ' + '.join(missing)) if missing else 'X is still registering your activity —'
            await message.channel.send(
                f"🎫 **ENTRY CHECK — @{handle}**\n\n{checklist}\n\n{steps} finish up, then drop your handle here again and I'll re-scan you in seconds. 🤖")
    except Exception as e:
        print('giveaway verify error:', e)

async def run_command(cmd, guild, log):
    a = cmd.get('action')
    if a == 'list_channels':
        log.append('channels: ' + ' | '.join(f'{c.name} ({c.id})' for c in guild.text_channels))
    elif a == 'rename_channel':
        ch = find_channel(guild, cmd['channel'])
        old = ch.name
        await ch.edit(name=cmd['name'])
        log.append(f'renamed #{old} -> {cmd["name"]}')
    elif a == 'set_topic':
        ch = find_channel(guild, cmd['channel'])
        await ch.edit(topic=cmd['topic'])
        log.append(f'topic set on #{ch.name}')
    elif a == 'post_and_pin':
        ch = find_channel(guild, cmd['channel'])
        m = await ch.send(cmd['content'])
        await m.pin()
        log.append(f'posted+pinned in #{ch.name}')
    elif a == 'set_permissions':
        ch = find_channel(guild, cmd['channel'])
        role = find_role(guild, cmd['role'])
        ow = discord.PermissionOverwrite()
        for p in cmd.get('allow', []):
            setattr(ow, p, True)
        for p in cmd.get('deny', []):
            setattr(ow, p, False)
        await ch.set_permissions(role, overwrite=ow)
        log.append(f'perms set on #{ch.name} for role {role.name}')
    elif a == 'giveaway_winner':
        ch = find_channel(guild, cmd.get('channel', 'giveaway'))
        target = None
        async for m in ch.history(limit=100):
            if m.author == client.user and '\U0001F381' in (m.content or ''):
                target = m
                break
        if target is None:
            log.append('giveaway_winner: no giveaway post found')
        else:
            entrants = []
            for react in target.reactions:
                if str(react.emoji) == '\U0001F389':
                    async for u in react.users():
                        if not u.bot:
                            entrants.append(u)
            if not entrants:
                await ch.send('\U0001F381 Giveaway closed — no valid entries this round. New one starts now!')
                log.append('giveaway_winner: 0 entries')
            else:
                w = random.choice(entrants)
                prize = cmd.get('prize', 'a FREE month of \U0001F512 Lock Room')
                await ch.send(f"\U0001F381 **GIVEAWAY WINNER** \U0001F389\n\nCongratulations {w.mention} — you won **{prize}**!\n\nThe captain will get you set up within 24h. Thanks to all {len(entrants)} entries — the next giveaway starts RIGHT NOW \U0001F440")
                log.append(f'giveaway_winner: {w.name} ({w.id}) from {len(entrants)} entries')
    elif a == 'setup_scan_role':
        role = discord.utils.get(guild.roles, name='🛰️ Scan Alerts')
        if role is None:
            role = await guild.create_role(name='🛰️ Scan Alerts', mentionable=True, reason='scan alert opt-in')
            log.append(f'created role {role.id}')
        ch = find_channel(guild, 'general-chat')
        msg = await ch.send("🛰️ **WANT THE HEADS-UP?**\nReact with 🛰️ and you'll get one quiet ping before each scan (6x daily — T-60 and T-10 only, nothing else). Remove your reaction anytime to opt out. No spam, just the warning. 🤖")
        try:
            await msg.add_reaction('🛰️')
        except Exception:
            pass
        try:
            await msg.pin()
        except Exception:
            pass
        state = await asyncio.to_thread(get_state)
        state['scan_role_id'] = role.id
        state['scan_role_msg'] = msg.id
        await asyncio.to_thread(gh_put, 'bot_state.json', state, 'scan role setup')
        log.append('scan role + opt-in post live')
    elif a == 'export_entries':
        ch = find_channel(guild, cmd.get('channel', 'giveaway'))
        if not ch:
            log.append('export_entries: giveaway channel not found')
        else:
            tier_of = {}
            for m in guild.members:
                names = [r.name for r in m.roles]
                if any('Whale' in n or '🐋' in n for n in names):
                    tier_of[m.id] = ('whale', 5)
                elif any('Sharp' in n or '📊' in n for n in names):
                    tier_of[m.id] = ('sharp', 3)
                elif any('Lock' in n or '🔒' in n for n in names):
                    tier_of[m.id] = ('lock', 2)
            entries = {}
            async for msg in ch.history(limit=400):
                if msg.author.bot:
                    continue
                pats = list(re.findall(r'@([A-Za-z0-9_]{4,15})\b', msg.content or ''))
                pats += re.findall(r'(?:https?://)?(?:www\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{4,15})', msg.content or '', flags=re.I)
                for h in pats:
                    key = h.lower()
                    if key in ('thelineshift', 'everyone', 'here', 'status', 'home', 'search', 'explore', 'i'):
                        continue
                    tname, weight = tier_of.get(msg.author.id, ('free', 1))
                    cur = entries.get(key)
                    if cur is None or weight > cur['weight']:
                        entries[key] = {'handle': h, 'discord': str(msg.author), 'discord_id': str(msg.author.id),
                                        'tier': tname, 'weight': weight}
            pool = []
            for e in entries.values():
                pool += [e['handle']] * e['weight']
            doc = {'exported': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                   'unique': len(entries), 'weighted_pool': len(pool),
                   'entries': sorted(entries.values(), key=lambda x: x['handle'].lower()), 'pool': sorted(pool)}
            await asyncio.to_thread(gh_put, 'giveaway_entries.json', doc, 'entry export', 'main')
            log.append(f"exported {len(entries)} unique entries ({len(pool)} weighted tickets) -> giveaway_entries.json")
    elif a == 'purge_channel':
        ch = find_channel(guild, cmd['channel'])
        if not ch:
            log.append(f'purge_channel: #{cmd["channel"]} not found')
        else:
            n = 0
            async for m in ch.history(limit=int(cmd.get('limit', 50))):
                if cmd.get('bots_only') and not m.author.bot:
                    continue
                if cmd.get('marker') and cmd['marker'].lower() not in (m.content or '').lower():
                    continue
                try:
                    await m.delete()
                    n += 1
                except Exception:
                    pass
            log.append(f'purged {n} messages in #{ch.name}')
    elif a == 'make_invite':
        ch = find_channel(guild, cmd.get('channel', 'general-chat'))
        inv = await ch.create_invite(max_age=0, max_uses=0, reason='permanent invite for X/giveaways')
        log.append(f'INVITE: https://discord.gg/{inv.code}')
    elif a == 'set_server_icon':
        try:
            ref = cmd.get('ref', 'main')
            path = cmd.get('path', 'assets/server_icon.png')
            remote = await asyncio.to_thread(gh_get, path, ref)
            icon_bytes = base64.b64decode(remote['content'])
            await guild.edit(icon=icon_bytes, reason='SHiFT server icon - brand mark')
            log.append(f'server icon set from {path} ({len(icon_bytes)} bytes)')
        except Exception as e:
            log.append(f'set_server_icon FAILED: {type(e).__name__}: {e}')
    elif a == 'clean_general':
        ch = find_channel(guild, 'general-chat')
        if not ch:
            log.append('clean_general: channel not found')
        else:
            seen, dups, finale = set(), [], None
            async for m in ch.history(limit=60):
                txt = (m.content or '')
                if 'crunching' in txt and 'parameters' in txt:
                    keyt = txt[:60]
                    if keyt in seen and m.author.bot:
                        dups.append(m)
                    else:
                        seen.add(keyt)
                if 'SCAN COMPLETE' in txt and 'MAKEUP' in txt.upper() and finale is None:
                    finale = m
            for m in dups:
                try:
                    await m.delete()
                    log.append('deleted duplicate ANALYZING post')
                except Exception as e:
                    log.append(f'dup delete fail: {e}')
            if finale is not None:
                try:
                    fixed = (finale.content or '').replace('card already live from 8 AM (Mariners ML 2u, 3:40 PM ET) — no forced adds',
                                                           'card already live from the 8 AM scan — no forced adds')
                    fixed = fixed.replace('Mariners ML 2u live', 'whale card live')
                    if fixed != (finale.content or ''):
                        await finale.delete()
                        await ch.send(fixed)
                        log.append('finale reposted without whale leak')
                    else:
                        log.append('finale had no leak text')
                except Exception as e:
                    log.append(f'finale fix fail: {e}')
            if not dups and finale is None:
                log.append('clean_general: nothing to fix')
    elif a == 'audit_channels':
        for c2 in guild.text_channels:
            topic = (c2.topic or '')[:60]
            log.append(f'#{c2.name} ({c2.id}) topic: {topic or "NONE"}')
        bots = [m for m in guild.members if m.bot]
        for b in bots:
            av = 'custom' if b.avatar else 'DEFAULT'
            log.append(f'BOT {b.name} | nick: {b.nick} | avatar: {av}')
    elif a == 'x_timeline':
        c = x_creds_load()
        try:
            url = 'https://api.x.com/2/users/1831457082828021760/tweets?max_results=100&tweet.fields=created_at,referenced_tweets'
            req = urllib.request.Request(url, headers={'Authorization': f"Bearer {c['bearer_token']}"})
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.load(r)
            for t in d.get('data', []):
                refs = t.get('referenced_tweets', [])
                kind = refs[0].get('type', 'post') if refs else 'post'
                log.append(f"{t['id']} | {t.get('created_at', '')[:16]} | {kind} | {t['text'][:70]}")
            if not d.get('data'):
                log.append('x_timeline: no posts')
        except Exception as e:
            body = ''
            if hasattr(e, 'read'):
                try: body = e.read()[:200]
                except Exception: pass
            log.append(f'x_timeline FAIL: {e} {body}')
    elif a == 'x_delete':
        c = x_creds_load()
        if time.time() > c.get('oauth2_expires_at', 0):
            c = await asyncio.to_thread(x_oauth2_refresh, c)
        for tid in cmd.get('ids', []):
            try:
                req = urllib.request.Request(f'https://api.x.com/2/tweets/{tid}', method='DELETE',
                                             headers={'Authorization': f"Bearer {c['oauth2_access']}"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    json.load(r)
                log.append(f'deleted post {tid}')
            except Exception as e:
                body = ''
                if hasattr(e, 'read'):
                    try: body = e.read()[:150]
                    except Exception: pass
                log.append(f'delete {tid} FAIL: {e} {body}')
    elif a == 'x_media_probe':
        try:
            remote = await asyncio.to_thread(gh_get, cmd.get('path', 'assets/giveaway_card.png'), cmd.get('ref', 'main'))
            img = base64.b64decode(remote['content'])
            c = x_creds_load()
            if time.time() > c.get('oauth2_expires_at', 0):
                c = await asyncio.to_thread(x_oauth2_refresh, c)
            mid = await asyncio.to_thread(x_upload_media, img, 'image/png', c)
            log.append(f'x_media_probe OK: media_id {mid} ({len(img)} bytes) — native image posts LIVE')
        except Exception as e:
            body = ''
            if hasattr(e, 'read'):
                try: body = e.read()[:200]
                except Exception: pass
            log.append(f'x_media_probe FAIL: {e} {body}')
    elif a == 'verify_entry':
        # manual entry verification: check a handle's follow/like/repost vs the live giveaway post
        handle = cmd.get('handle', '').lstrip('@')
        ch = find_channel(guild, cmd.get('channel', 'giveaway'))
        if not handle or not ch:
            log.append('verify_entry: need handle + channel')
        else:
            try:
                c = x_creds_load()
                bt = c['bearer_token']
                state = await asyncio.to_thread(get_state)
                post_id = (state or {}).get('giveaway_x_post', '')
                our_id = '1831457082828021760'
                if time.time() > c.get('oauth2_expires_at', 0):
                    c = await asyncio.to_thread(x_oauth2_refresh, c)
                uat = c.get('oauth2_access', bt)
                try:
                    u = await asyncio.to_thread(x_get_json, f'https://api.x.com/2/users/by/username/{handle}', bt)
                    uid = str(u.get('data', {}).get('id') or '')
                except Exception:
                    uid = ''
                if not uid:
                    await ch.send(f"🤖 SHiFT entry check: can't find an X account **@{handle}** — double-check the spelling and drop it again.")
                else:
                    followed = await asyncio.to_thread(gw_followed, uid, uat)
                    try:
                        liked = uid in await asyncio.to_thread(gw_user_set, f'https://api.x.com/2/tweets/{post_id}/liking_users?max_results=100', uat)
                    except Exception as _e:
                        log.append(f'verify like-check err: {str(_e)[:80]}')
                        liked = None
                    try:
                        reposted = uid in await asyncio.to_thread(gw_user_set, f'https://api.x.com/2/tweets/{post_id}/retweeted_by?max_results=100', uat)
                    except Exception as _e:
                        log.append(f'verify repost-check err: {str(_e)[:80]}')
                        reposted = None
                    def ic(ok, label):
                        return f"{'✅' if ok else ('❌' if ok is False else '❓')} {label}"
                    checklist = "\n".join([ic(followed, 'Follow @TheLineShift'), ic(liked, 'Like the giveaway post'), ic(reposted, 'Repost the giveaway post')])
                    missing = []
                    if followed is False: missing.append('follow @TheLineShift')
                    if liked is False: missing.append('like the giveaway post')
                    if reposted is False: missing.append('repost the giveaway post')
                    if not missing and followed and liked and reposted:
                        conf = await asyncio.to_thread(gh_get_json_ref, 'giveaway_confirmed.json', QUEUE_BRANCH)
                        if handle.lower() in (conf or {}):
                            await ch.send(f"🎫 **@{handle}** — already locked in the pool. Sunday 6 PM ET. 🤖")
                            log.append(f'verify_entry: @{handle} already confirmed')
                        else:
                            mult, dname, did, tkey = 1, '', '', 'free'
                            ent = await asyncio.to_thread(gh_get_json_ref, 'giveaway_entries.json', 'main')
                            for e in (ent or {}).get('entries', []):
                                if e.get('handle', '').lower() == handle.lower():
                                    mult = int(e.get('weight', 1)); dname = e.get('discord', ''); did = e.get('discord_id', '')
                                    tkey = e.get('tier', 'free')
                                    break
                            conf = await asyncio.to_thread(gh_get_json_ref, 'giveaway_confirmed.json', QUEUE_BRANCH)
                            conf = conf or {}
                            conf[handle.lower()] = {
                                'handle': handle, 'discord': dname, 'discord_id': did,
                                'mult': mult, 'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
                            await asyncio.to_thread(gh_put, 'giveaway_confirmed.json', conf, 'giveaway confirm ' + handle, QUEUE_BRANCH)
                            await ch.send(f"🎫 **ENTRY CONFIRMED — @{handle}**\n\n{checklist}\n🎟️ **Tickets: {mult}x — {TIER_ROOM.get(tkey, tkey)}**\n\nDraw: Sunday 6 PM ET — provably fair, paid on-chain. 🤖")
                            log.append(f'verify_entry: CONFIRMED @{handle} ({mult}x)')
                    else:
                        steps = (f"**{len(missing)} step{'s' if len(missing) > 1 else ''} left:** " + ' + '.join(missing)) if missing else 'X is still registering your activity —'
                        await ch.send(f"🎫 **ENTRY CHECK — @{handle}**\n\n{checklist}\n\n{steps} finish up, then drop your handle here again and I'll re-scan you in seconds. 🤖")
                        log.append(f'verify_entry: @{handle} incomplete ({len(missing)} left)')
            except Exception as e:
                log.append(f'verify_entry FAIL: {e}')
    elif a == 'x_followers_probe':
        try:
            c = x_creds_load()
            if time.time() > c.get('oauth2_expires_at', 0):
                c = await asyncio.to_thread(x_oauth2_refresh, c)
            d = await asyncio.to_thread(x_get_json, 'https://api.x.com/2/users/1831457082828021760/followers?max_results=100', c.get('oauth2_access', ''))
            ids = [str(x.get('id')) for x in d.get('data', [])]
            log.append(f'x_followers_probe OK: {len(ids)} followers readable, next={bool(d.get("meta", {}).get("next_token"))}')
        except Exception as e:
            body = ''
            if hasattr(e, 'read'):
                try: body = e.read()[:150]
                except Exception: pass
            log.append(f'x_followers_probe FAIL: {e} {body}')
    elif a == 'x_follow':
        try:
            c = x_creds_load()
            if time.time() > c.get('oauth2_expires_at', 0):
                c = await asyncio.to_thread(x_oauth2_refresh, c)
            uname = cmd.get('username', '').lstrip('@')
            req = urllib.request.Request(f'https://api.x.com/2/users/by/username/{uname}',
                                         headers={'Authorization': f"Bearer {c['bearer_token']}"})
            with urllib.request.urlopen(req, timeout=20) as r:
                uid = json.load(r)['data']['id']
            req = urllib.request.Request('https://api.x.com/2/users/1831457082828021760/following',
                                         data=json.dumps({'target_user_id': uid}).encode(), method='POST',
                                         headers={'Authorization': f"Bearer {c['oauth2_access']}", 'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=20) as r:
                json.load(r)
            log.append(f'followed @{uname}')
        except Exception as e:
            body = ''
            if hasattr(e, 'read'):
                try: body = e.read()[:150]
                except Exception: pass
            log.append(f'x_follow FAIL: {e} {body}')
    elif a == 'x_like':
        try:
            c = x_creds_load()
            if time.time() > c.get('oauth2_expires_at', 0):
                c = await asyncio.to_thread(x_oauth2_refresh, c)
            req = urllib.request.Request('https://api.x.com/2/users/1831457082828021760/likes',
                                         data=json.dumps({'tweet_id': cmd['id']}).encode(), method='POST',
                                         headers={'Authorization': f"Bearer {c['oauth2_access']}", 'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=20) as r:
                json.load(r)
            log.append(f"liked {cmd['id']}")
        except Exception as e:
            body = ''
            if hasattr(e, 'read'):
                try: body = e.read()[:150]
                except Exception: pass
            log.append(f'x_like FAIL: {e} {body}')
    elif a == 'x_post_text':
        try:
            res = await asyncio.to_thread(x_post, cmd['text'])
            tid = res.get('data', {}).get('id') if res else None
            log.append(f'x_post_text OK: id {tid}')
            if cmd.get('tag') == 'giveaway' and tid:
                st2 = await asyncio.to_thread(get_state)
                if st2 is not None:
                    st2['giveaway_x_post'] = tid
                    await asyncio.to_thread(gh_put, 'bot_state.json', st2, 'giveaway x post id')
        except Exception as e:
            log.append(f'x_post_text FAIL: {e}')
    elif a == 'x_refresh':
        try:
            c = x_creds_load()
            c = await asyncio.to_thread(x_oauth2_refresh, c)
            log.append(f"x_refresh OK: token now expires {time.strftime('%H:%M UTC', time.gmtime(c.get('oauth2_expires_at', 0)))}")
        except Exception as e:
            body = ''
            if hasattr(e, 'read'):
                try: body = e.read()[:250]
                except Exception: pass
            log.append(f'x_refresh FAIL: {e} {body}')
    elif a == 'x_me':
        try:
            c = x_creds_load()
            if time.time() > c.get('oauth2_expires_at', 0):
                c = await asyncio.to_thread(x_oauth2_refresh, c)
            req = urllib.request.Request('https://api.x.com/2/users/me',
                                         headers={'Authorization': f"Bearer {c['oauth2_access']}"})
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.load(r)
            log.append(f"x_me OK: @{d.get('data', {}).get('username')} id {d.get('data', {}).get('id')} — oauth2 user token LIVE")
        except Exception as e:
            body = ''
            if hasattr(e, 'read'):
                try: body = e.read()[:250]
                except Exception: pass
            log.append(f'x_me FAIL: {e} {body}')
    elif a == 'collect_metrics':
        try:
            counts = {'members': guild.member_count}
            for word, key in [('Lock', 'lock'), ('Sharp', 'sharp'), ('Whale', 'whale')]:
                n = sum(1 for mem in guild.members if any(word.lower() in r.name.lower() for r in mem.roles))
                counts[key] = n
            snap = {'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), **counts}
            xc = x_creds()
            bt = (xc.get('bearer') or '').strip()
            if bt:
                xr = xrequests.get('https://api.x.com/2/users/1831457082828021760?user.fields=public_metrics',
                                   headers={'Authorization': f'Bearer {bt}'}, timeout=15)
                if xr.status_code == 200:
                    d = xr.json()['data']['public_metrics']
                    snap['x_followers'] = d['followers_count']
                    snap['x_tweets'] = d['tweet_count']
            m = await asyncio.to_thread(gh_get_json, 'metrics.json')
            m = m or {'snapshots': []}
            m.setdefault('snapshots', []).append(snap)
            m['snapshots'] = m['snapshots'][-400:]
            await asyncio.to_thread(gh_put, 'metrics.json', m, 'metrics snapshot')
            log.append(f'metrics: {snap}')
        except Exception as e:
            log.append(f'metrics FAIL: {e}')
    elif a == 'harden_guild':
        try:
            await guild.edit(explicit_content_filter=discord.ContentFilter.all_members, reason='SHiFT harden')
            log.append('harden_guild: explicit content filter = ALL members')
        except Exception as e:
            log.append(f'harden_guild filter FAIL: {e}')
        try:
            trig = discord.AutoModTrigger(type=discord.AutoModRuleTriggerType.mention_spam, mention_limit=5)
            acts = [discord.AutoModRuleAction(type=discord.AutoModRuleActionType.block_message)]
            await guild.create_automod_rule(name='SHiFT mention guard', event_type=discord.AutoModRuleEventType.message_send,
                                            trigger=trig, actions=acts, enabled=True, reason='harden')
            log.append('harden_guild: AutoMod mention-spam rule ON')
        except Exception as e:
            log.append(f'harden_guild automod mention FAIL: {e}')
        try:
            trig2 = discord.AutoModTrigger(type=discord.AutoModRuleTriggerType.keyword,
                keyword_filter=['dm me','send me a dm','d.m me','telegram','t.me/','whatsapp','free nitro','airdrop','double your','forex','guaranteed profit','claim your'])
            acts2 = [discord.AutoModRuleAction(type=discord.AutoModRuleActionType.block_message)]
            await guild.create_automod_rule(name='SHiFT scam filter', event_type=discord.AutoModRuleEventType.message_send,
                                            trigger=trig2, actions=acts2, enabled=True, reason='harden')
            log.append('harden_guild: AutoMod scam-keyword rule ON')
        except Exception as e:
            log.append(f'harden_guild automod keyword FAIL: {e}')
    elif a == 'lockdown_channels':
        def _frag(s):
            return ''.join(c for c in s.lower() if c.isalnum() or c == '-')
        def _tier_role(word):
            for r in guild.roles:
                if word.lower() in r.name.lower():
                    return r
            return None
        OPEN_SEND = ['general-chat', 'giveaway', 'issues']
        OPEN_READ = ['free-pick', 'receipts', 'scan-feed', 'updates']
        STAFF_ONLY = ['shift-lab']
        PAID = {'daily-locks': ['Lock', 'Sharp', 'Whale'], 'all-picks': ['Sharp', 'Whale'],
                'every-play': ['Whale'], 'weekly-analytics': ['Sharp', 'Whale'],
                'monthly-deepdive': ['Whale'], '100-to-1000': ['Lock', 'Sharp', 'Whale']}
        n = 0
        for ch in guild.text_channels:
            nm = _frag(ch.name)
            try:
                if any(_frag(k) in nm for k in STAFF_ONLY):
                    await ch.set_permissions(guild.default_role, overwrite=discord.PermissionOverwrite(view_channel=False))
                    for w in ('Lock', 'Sharp', 'Whale'):
                        r = _tier_role(w)
                        if r:
                            await ch.set_permissions(r, overwrite=discord.PermissionOverwrite(view_channel=False))
                    n += 1
                elif any(_frag(k) in nm for k in OPEN_SEND):
                    await ch.set_permissions(guild.default_role, overwrite=discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, mention_everyone=False))
                    n += 1
                elif any(_frag(k) in nm for k in OPEN_READ):
                    await ch.set_permissions(guild.default_role, overwrite=discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True, mention_everyone=False))
                    n += 1
                else:
                    hit = None
                    for key, roles in PAID.items():
                        if _frag(key) in nm:
                            hit = roles
                            break
                    if hit is not None:
                        await ch.set_permissions(guild.default_role, overwrite=discord.PermissionOverwrite(view_channel=False))
                        for w in ('Lock', 'Sharp', 'Whale'):
                            r = _tier_role(w)
                            if not r:
                                continue
                            if w in hit:
                                can_send = key in ('daily-locks', 'all-picks', 'every-play')
                                await ch.set_permissions(r, overwrite=discord.PermissionOverwrite(view_channel=True, send_messages=can_send, read_message_history=True, mention_everyone=False))
                            else:
                                await ch.set_permissions(r, overwrite=discord.PermissionOverwrite(view_channel=False))
                        n += 1
            except Exception as e:
                log.append(f'lockdown FAIL #{ch.name}: {e}')
        log.append(f'lockdown_channels: {n} channels locked — tiers enforced, @everyone pings disabled server-wide')
    elif a == 'delete_where':
        try:
            ch = find_channel(guild, cmd['channel'])
            n = 0
            if ch:
                async for m in ch.history(limit=cmd.get('limit', 15)):
                    if cmd['contains'] in (m.content or '') and (not cmd.get('author') or cmd['author'] in (m.author.name or '')):
                        await m.delete()
                        n += 1
                        log.append(f'delete_where: removed message {m.id} in #{ch.name}')
                        if n >= cmd.get('max', 1):
                            break
            if n == 0:
                log.append('delete_where: no matching message found')
        except Exception as e:
            log.append(f'delete_where FAIL: {e}')
    elif a == 'x_oauth1_me':
        for name, ck, cs, at, ats in x_oauth1_sets(x_creds_load()):
            try:
                hdr = x_oauth1_sign('GET', 'https://api.x.com/2/users/me', ck, cs, at, ats)
                req = urllib.request.Request('https://api.x.com/2/users/me', headers={'Authorization': hdr})
                with urllib.request.urlopen(req, timeout=20) as r:
                    d = json.load(r)
                log.append(f"x_oauth1_me [{name}] OK: @{d.get('data', {}).get('username')} id {d.get('data', {}).get('id')}")
            except urllib.error.HTTPError as e:
                try:
                    eb = e.read()[:220]
                except Exception:
                    eb = b''
                log.append(f'x_oauth1_me [{name}] HTTP {e.code}: {eb}')
            except Exception as e:
                log.append(f'x_oauth1_me [{name}] FAIL: {e}')
    elif a == 'x_media_test':
        try:
            img = await asyncio.to_thread(gh_raw_bytes, cmd.get('path', 'assets/giveaway_card.png'), cmd.get('ref', 'main'))
            if len(img) < 1000:
                raise Exception(f'image fetch too small: {len(img)} bytes')
            cname, mid = await asyncio.to_thread(x_upload_media_oauth1, img)
            log.append(f'x_media_test OK: media_id {mid} via {cname} ({len(img)} bytes) — native image posts LIVE')
        except Exception as e:
            log.append(f'x_media_test FAIL: {e}')
    elif a == 'x_post_media_native':
        try:
            img = await asyncio.to_thread(gh_raw_bytes, cmd.get('path', 'assets/giveaway_card.png'), cmd.get('ref', 'main'))
            if len(img) < 1000:
                raise Exception(f'image fetch too small: {len(img)} bytes')
            cname, mid = await asyncio.to_thread(x_upload_media_oauth1, img)
            if cname == 'oauth2':
                res = await asyncio.to_thread(x_post_media_oauth2, cmd['text'], mid)
            else:
                res = await asyncio.to_thread(x_post_media_oauth1, cmd['text'], mid, cname)
            tid = res.get('data', {}).get('id') if res else None
            log.append(f'x_post_media_native OK: tweet {tid} with media {mid} via {cname}')
            if cmd.get('tag') == 'giveaway' and tid:
                st2 = await asyncio.to_thread(get_state)
                if st2 is not None:
                    st2['giveaway_x_post'] = str(tid)
                    await asyncio.to_thread(gh_put, 'bot_state.json', st2, 'giveaway x post id (native media)')
        except Exception as e:
            log.append(f'x_post_media_native FAIL: {e}')
    elif a == 'x_post_media':
        try:
            remote = await asyncio.to_thread(gh_get, cmd.get('path', 'assets/giveaway_card.png'), cmd.get('ref', 'main'))
            img = base64.b64decode(remote['content'])
            c = x_creds_load()
            if time.time() > c.get('oauth2_expires_at', 0):
                c = await asyncio.to_thread(x_oauth2_refresh, c)
            media_id = await asyncio.to_thread(x_upload_media, img, 'image/png', c)
            body = json.dumps({'text': cmd['text'], 'media': {'media_ids': [media_id]}}).encode()
            req = urllib.request.Request('https://api.x.com/2/tweets', data=body, method='POST',
                                         headers={'Authorization': f"Bearer {c['oauth2_access']}", 'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=20) as r:
                res = json.load(r)
            tid = res.get('data', {}).get('id')
            log.append(f'x_post_media OK: id {tid}')
            if cmd.get('tag') == 'giveaway' and tid:
                st2 = await asyncio.to_thread(get_state)
                if st2 is not None:
                    st2['giveaway_x_post'] = tid
                    await asyncio.to_thread(gh_put, 'bot_state.json', st2, 'giveaway x post id')
        except Exception as e:
            body = ''
            if hasattr(e, 'read'):
                try: body = e.read()[:200]
                except Exception: pass
            log.append(f'x_post_media FAIL: {e} {body}')
    elif a == 'x_diag1':
        try:
            import hmac as _hmac, hashlib as _hl, secrets as _sc
            from urllib.parse import quote as _qq
            c = x_creds_load()
            url = 'https://api.twitter.com/1.1/account/verify_credentials.json'
            op = {'oauth_consumer_key': c['api_key'], 'oauth_nonce': _sc.token_hex(16),
                  'oauth_signature_method': 'HMAC-SHA1', 'oauth_timestamp': str(int(time.time())),
                  'oauth_token': c['access_token'], 'oauth_version': '1.0'}
            q = lambda s: _qq(str(s), safe='')
            base = '&'.join(['GET', q(url), q('&'.join(f'{q(k)}={q(v)}' for k, v in sorted(op.items())))])
            key = f"{q(c['api_secret'])}&{q(c['access_token_secret'])}"
            op['oauth_signature'] = base64.b64encode(_hmac.new(key.encode(), base.encode(), _hl.sha1).digest()).decode()
            hdr = 'OAuth ' + ', '.join(f'{k}="{q(v)}"' for k, v in sorted(op.items()))
            req = urllib.request.Request(url, headers={'Authorization': hdr})
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.load(r)
            log.append(f"x_diag1 v1.1 OK: @{d.get('screen_name')} — bio/logo endpoints reachable")
        except Exception as e:
            body = ''
            if hasattr(e, 'read'):
                try: body = e.read()[:200]
                except Exception: pass
            log.append(f'x_diag1 v1.1 FAIL: {e} {body}')
    elif a == 'x_link_scan':
        target = None
        for tch in guild.text_channels:
            if 'updates' in tch.name:
                target = tch
                break
        if target is None:
            log.append('x_link_scan: updates channel not found')
        else:
            found = False
            async for m in target.history(limit=50):
                cont = (m.content or '')
                if m.author.bot or 'thelineshift.com' not in cont or 'code=' not in cont:
                    continue
                url = cont.strip().strip('`').strip('<>').split()[0]
                if x_creds_load().get('oauth2_access'):
                    log.append('already linked; skipping exchange')
                else:
                    await run_command({'action': 'x_link_finish', 'url': url}, guild, log)
                try:
                    await m.delete()
                    log.append('deleted link message from #updates')
                except Exception as e:
                    log.append(f'delete failed: {e}')
                found = True
            if not found:
                log.append('x_link_scan: no link message found in #updates')
    elif a == 'x_link_start':
        import secrets as _s, hashlib as _h
        from urllib.parse import urlencode as _ue
        ver = _s.token_urlsafe(64)[:64]
        ch = base64.urlsafe_b64encode(_h.sha256(ver.encode()).digest()).decode().rstrip('=')
        pk = {'verifier': ver, 'state': _s.token_hex(8)}
        await asyncio.to_thread(gh_put, 'x_pkce.json', pk, 'pkce link')
        c = x_creds_load()
        q = _ue({'response_type': 'code', 'client_id': c.get('client_id', ''), 'redirect_uri': X_REDIRECT,
                 'scope': 'tweet.read tweet.write users.read follows.read follows.write like.read like.write media.write offline.access', 'state': pk['state'],
                 'code_challenge': ch, 'code_challenge_method': 'S256'})
        log.append('AUTH URL: https://twitter.com/i/oauth2/authorize?' + q)
    elif a == 'x_link_finish':
        from urllib.parse import urlparse, parse_qs, urlencode as _ue2
        qs = parse_qs(urlparse(cmd['url']).query)
        code = qs.get('code', [None])[0]
        if not code:
            log.append('x_link_finish: no code in URL')
        else:
            c = x_creds_load()
            try:
                pk = json.loads(base64.b64decode(gh_get('x_pkce.json', ref=QUEUE_BRANCH)['content']).decode())
            except Exception:
                pk = {}
            basic = base64.b64encode(f"{c['client_id']}:{c['client_secret']}".encode()).decode()
            data = {'grant_type': 'authorization_code', 'code': code, 'redirect_uri': X_REDIRECT,
                    'code_verifier': pk.get('verifier', ''), 'client_id': c['client_id']}
            req = urllib.request.Request('https://api.x.com/2/oauth2/token',
                                         data=_ue2(data).encode(), method='POST',
                                         headers={'Content-Type': 'application/x-www-form-urlencoded',
                                                  'Authorization': f'Basic {basic}'})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    t = json.load(r)
                c['oauth2_access'] = t['access_token']
                c['oauth2_refresh'] = t.get('refresh_token', '')
                c['oauth2_expires_at'] = time.time() + t.get('expires_in', 7200) - 120
                await asyncio.to_thread(gh_put, 'x_creds.json', c, 'oauth2 user token linked')
                res = None
                log.append('x_link_finish OK: tokens stored — X user link LIVE')
                try:
                    hello = cmd.get('text') or f'🛰️ SHiFT native X link online ({time.strftime("%H:%M UTC")}).'
                    res = await asyncio.to_thread(x_post_native, hello)
                    log.append(f'x_link_finish hello-post OK: tweet id {res.get("data", {}).get("id") if res else None}')
                except Exception as he:
                    log.append(f'x_link_finish hello-post skipped (link still LIVE): {he}')
            except urllib.error.HTTPError as e:
                log.append(f'x_link_finish FAIL: HTTP {e.code}: {e.read()[:250]}')
            except Exception as e:
                log.append(f'x_link_finish FAIL: {e}')
    elif a == 'x_diag':
        c = x_creds_load()
        # (a) bearer app-only read
        try:
            req = urllib.request.Request('https://api.x.com/2/users/by/username/TheLineShift',
                                         headers={'Authorization': f"Bearer {c.get('bearer_token', '')}"})
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.load(r)
            log.append(f"x_diag bearer OK: id {d.get('data', {}).get('id')}")
        except Exception as e:
            body = ''
            if hasattr(e, 'read'):
                try: body = e.read()[:200]
                except Exception: pass
            log.append(f'x_diag bearer FAIL: {e} {body}')
        # (b) oauth1 user-context read
        try:
            import hmac, hashlib, secrets
            from urllib.parse import quote as _uq
            url = 'https://api.x.com/2/users/me'
            op = {'oauth_consumer_key': c['api_key'], 'oauth_nonce': secrets.token_hex(16),
                  'oauth_signature_method': 'HMAC-SHA1', 'oauth_timestamp': str(int(time.time())),
                  'oauth_token': c['access_token'], 'oauth_version': '1.0'}
            q = lambda s: _uq(str(s), safe='')
            base = '&'.join(['GET', q(url), q('&'.join(f'{q(k)}={q(v)}' for k, v in sorted(op.items())))])
            key = f"{q(c['api_secret'])}&{q(c['access_token_secret'])}"
            op['oauth_signature'] = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
            hdr = 'OAuth ' + ', '.join(f'{k}="{q(v)}"' for k, v in sorted(op.items()))
            req = urllib.request.Request(url, headers={'Authorization': hdr})
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.load(r)
            log.append(f"x_diag oauth1 GET OK: @{d.get('data', {}).get('username')}")
        except Exception as e:
            body = ''
            if hasattr(e, 'read'):
                try: body = e.read()[:200]
                except Exception: pass
            log.append(f'x_diag oauth1 GET FAIL: {e} {body}')
    elif a == 'x_test':
        try:
            res = await asyncio.to_thread(x_post_native, cmd.get('text', '\U0001F6F0\uFE0F SHiFT native X link online. The board never sleeps.'))
            log.append(f'x_test OK: tweet id {res.get("data", {}).get("id") if res else None}')
        except Exception as e:
            log.append(f'x_test FAIL: {e}')
    elif a == 'set_icon':
        req = urllib.request.Request(cmd['url'], headers={'User-Agent': 'lineshift-bot'})
        data = urllib.request.urlopen(req, timeout=25).read()
        await guild.edit(icon=data)
        log.append('server icon updated')
    elif a == 'delete_channel_id':
        ch = guild.get_channel(int(cmd['id']))
        if ch:
            await ch.delete()
            log.append(f'deleted #{ch.name} ({cmd["id"]})')
        else:
            log.append(f'delete_channel_id: {cmd["id"]} not found')
    elif a == 'delete_channel':
        ch = find_channel(guild, cmd['channel'])
        await ch.delete()
        log.append(f'deleted #{ch.name}')
    elif a == 'create_channel':
        kwargs = {}
        if cmd.get('private_for'):
            role = find_role(guild, cmd['private_for'])
            if role:
                kwargs['overwrites'] = {guild.default_role: discord.PermissionOverwrite(view_channel=False),
                                        role: discord.PermissionOverwrite(view_channel=True)}
        ch = await guild.create_text_channel(cmd['name'], **kwargs)
        log.append(f'created #{ch.name}')
    elif a == 'list_roles':
        log.append('ROLES: ' + ' | '.join(
            f'{r.name} (pos={r.position}, members={len(r.members)})'
            for r in sorted(guild.roles, key=lambda x: x.position, reverse=True)))
    elif a == 'audit_roles':
        lines = []
        async for e in guild.audit_logs(action=discord.AuditLogAction.member_role_update, limit=25):
            t = getattr(e, 'target', None)
            if t is not None:
                lines.append(f'{getattr(t, "name", "?")}({getattr(t, "id", "?")}) by {e.user} at {e.created_at:%m-%d %H:%M}')
        log.append('AUDIT: ' + (' | '.join(lines) if lines else 'no member_role_update entries'))
    elif a == 'read_pins':
        ch = find_channel(guild, cmd['channel'])
        pins = await ch.pins()
        if not pins:
            log.append(f'no pins in #{ch.name}')
        for i, m in enumerate(pins):
            log.append(f'PIN{i} by {m.author}: {(m.content or "")[:400]}')
            for e in m.embeds:
                log.append(f'  EMBED title={e.title!r} desc={(e.description or "")[:600]!r}')
                for f in e.fields:
                    log.append(f'    FIELD {f.name!r}: {(f.value or "")[:300]!r}')
            for att in m.attachments:
                log.append(f'  ATTACH: {att.filename} {att.url[:120]}')
    elif a == 'replace_pinned':
        ch = find_channel(guild, cmd['channel'])
        pins = await ch.pins()
        marker = cmd.get('marker', '')
        removed = 0
        for m in pins:
            if not marker or marker.lower() in (m.content or '').lower() or len(pins) == 1:
                try:
                    await m.unpin()
                    removed += 1
                except Exception:
                    pass
                try:
                    await m.delete()
                    log.append('old pinned message deleted')
                except Exception:
                    log.append('old pinned message unpinned (could not delete - not mine)')
        m = await ch.send(cmd['content'])
        await m.pin()
        log.append(f'replaced pin in #{ch.name} (unpinned {removed})')
    elif a == 'read_recent':
        n = int(cmd.get('limit', 4))
        if cmd.get('channel') == 'all':
            targets = list(guild.text_channels)
        else:
            targets = [find_channel(guild, cmd['channel'])]
        for ch in targets:
            if ch is None:
                continue
            count = 0
            try:
                async for m in ch.history(limit=n):
                    log.append(f'#{ch.name} | {str(m.author)[:22]}: {(m.content or "")[:170]}')
                    count += 1
            except Exception as e:
                log.append(f'#{ch.name}: read error {e}')
            if count == 0:
                log.append(f'#{ch.name}: (empty)')
    elif a == 'read_full':
        n = int(cmd.get('limit', 6))
        ch = find_channel(guild, cmd['channel'])
        if ch is None:
            log.append(f"read_full: channel {cmd.get('channel')} not found")
        else:
            count = 0
            try:
                async for m in ch.history(limit=n):
                    body = (m.content or '')[:950].replace('\n', ' \\n ')
                    log.append(f'FULL #{ch.name} id={m.id} | {str(m.author)[:20]}: {body}')
                    count += 1
            except Exception as e:
                log.append(f'#{ch.name}: read_full error {e}')
            if count == 0:
                log.append(f'#{ch.name}: (empty)')
    elif a == 'delete_message':
        ch = find_channel(guild, cmd['channel'])
        marker = cmd.get('marker', '')
        done = False
        async for m in ch.history(limit=50):
            if marker.lower() in (m.content or '').lower():
                await m.delete()
                log.append(f'deleted message in #{ch.name} (marker match)')
                done = True
                break
        if not done:
            log.append(f'delete_message: marker not found in #{ch.name}')
    elif a == 'purge_system':
        total = 0
        for ch in guild.text_channels:
            try:
                async for m in ch.history(limit=30):
                    if m.type == discord.MessageType.pins_add:
                        await m.delete()
                        total += 1
            except Exception:
                pass
        log.append(f'purged {total} pin notices server-wide')
    elif a == 'check_giveaway':
        ch = find_channel(guild, cmd.get('channel', 'giveaway'))
        target = None
        async for m in ch.history(limit=100):
            if m.author == client.user and '\U0001F381' in (m.content or ''):
                target = m
                break
        if target is None:
            log.append('check_giveaway: no giveaway post found')
        else:
            n = 0
            for react in target.reactions:
                if str(react.emoji) == '\U0001F389':
                    n = react.count
            log.append(f'check_giveaway: post found, \U0001F389 reactions={n}')
    elif a == 'list_members':
        count = 0
        async for m in guild.fetch_members(limit=None):
            roles = [r.name for r in m.roles if r.name != '@everyone']
            log.append(f'{m.name} ({m.id}) joined {m.joined_at:%m-%d %H:%M} roles={roles}')
            count += 1
        log.append(f'total members: {count}')
    elif a == 'set_avatar':
        if cmd.get('path'):
            d = await asyncio.to_thread(gh_get, cmd['path'], 'main')
            data = base64.b64decode(d['content'])
        else:
            req = urllib.request.Request(cmd['url'], headers={'User-Agent': 'lineshift-bot'})
            data = urllib.request.urlopen(req, timeout=25).read()
        await client.user.edit(avatar=data)
        log.append(f'bot avatar updated ({len(data) // 1024} KB)')
    elif a == 'set_nick':
        await guild.me.edit(nick=cmd.get('nick', BOT_NICK))
        log.append(f'nick set -> {guild.me.nick}')
    elif a == 'set_status':
        await client.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=cmd.get('status', BOT_STATUS)))
        log.append('status set')
    elif a == 'make_webhook':
        ch = find_channel(guild, cmd['channel'])
        wh = await ch.create_webhook(name=cmd.get('name', 'TheLineShift Bot'))
        log.append(f'webhook for #{ch.name}: {wh.url}')
    elif a == 'audit_all':
        lines = []
        async for e in guild.audit_logs(limit=25):
            t = getattr(e, 'target', None)
            tn = getattr(t, 'name', None) or str(getattr(t, 'id', '?'))
            lines.append(f'{e.action.name} target={tn} by={e.user}')
        log.append('AUDITALL: ' + (' | '.join(lines) if lines else 'empty'))
    elif a == 'add_role':
        m = await resolve_member(guild, cmd.get('member', ''))
        role = find_role(guild, cmd.get('role', ''))
        if not m or not role:
            log.append(f'add_role FAILED member={cmd.get("member")} found={bool(m)} role={cmd.get("role")} found={bool(role)}')
        elif role in m.roles:
            log.append(f'{m.name} already has {role.name}')
        else:
            await m.add_roles(role, reason='LineShift manual role fix')
            log.append(f'added {role.name} -> {m.name}')
    elif a == 'remove_role':
        m = await resolve_member(guild, cmd.get('member', ''))
        role = find_role(guild, cmd.get('role', ''))
        if m and role and role in m.roles:
            await m.remove_roles(role, reason='LineShift manual role fix')
            log.append(f'removed {role.name} from {m.name}')
        else:
            log.append(f'remove_role skipped member_found={bool(m)} role_found={bool(role)}')
    elif a == 'member_roles':
        m = await resolve_member(guild, cmd.get('member', ''))
        if m:
            log.append(f'{m.name} roles: ' + ', '.join(r.name for r in m.roles))
        else:
            log.append(f'member not found: {cmd.get("member")}')
    elif a == 'fix_role_hierarchy':
        top = guild.me.top_role.position
        updates = {}
        whop = find_role(guild, 'whop')
        if whop and whop.id != guild.me.top_role.id and whop.position != top - 1:
            updates[whop] = top - 1
        pos = 1
        for key in ('lock', 'sharp', 'whale'):
            r = find_role(guild, key)
            if r:
                updates[r] = pos
                pos += 1
        if updates:
            await guild.edit_role_positions(updates, reason='LineShift hierarchy fix')
            log.append('hierarchy: ' + ', '.join(f'{r.name}->{p}' for r, p in updates.items()))
        else:
            log.append('hierarchy: nothing to move')
    else:
        log.append(f'unknown action: {a}')

@tasks.loop(seconds=60)
async def poll():
    try:
        if not GH_TOKEN:
            return
        data = await asyncio.to_thread(fetch_commands)
        if not data:
            return
        seq = data.get('seq', 0)
        state = await asyncio.to_thread(get_state)
        if not state:
            return
        if seq <= state.get('executed_seq', 0):
            return
        guild = client.guilds[0] if client.guilds else None
        if not guild:
            return
        done_cmd = state.get('executed_cmd_seq', 0)
        log = [f'seq {seq} executed {time.strftime("%Y-%m-%d %H:%M UTC")}']
        ran = 0
        for cmd in data.get('commands', []):
            if cmd.get('seq', 0) <= done_cmd:
                continue
            try:
                await run_command(cmd, guild, log)
                ran += 1
                done_cmd = max(done_cmd, cmd.get('seq', 0))
            except Exception as e:
                log.append(f'ERROR {cmd.get("action")}: {e}')
        state['executed_cmd_seq'] = done_cmd
        if ran == 0:
            log.append('no new commands')
        state['executed_seq'] = seq
        state['last_log'] = log
        try:
            await asyncio.to_thread(gh_put, 'bot_state.json', state, f'bot executed seq {seq}')
        except Exception as e:
            print('state push failed:', e)
    except Exception as e:
        print('poll error:', e)

SCAN_HOURS_ET = [0, 4, 8, 12, 16, 20]
EVENT_HOURS_UTC = [0, 4, 8, 12, 16, 20]

@tasks.loop(seconds=60)
async def countdown():
    try:
        if not client.guilds:
            return
        guild = client.guilds[0]
        now = time.gmtime()
        et_h = (now.tm_hour - 4) % 24
        et_m = now.tm_min
        marker = None
        for h in SCAN_HOURS_ET:
            if et_h == (h - 1) % 24 and et_m == 0:
                marker = ('60', h)
            elif et_h == (h - 1) % 24 and et_m == 50:
                marker = ('10', h)
        if not marker:
            return
        daykey = f'{now.tm_year}{now.tm_mon:02d}{now.tm_mday:02d}-{et_h:02d}{et_m:02d}'
        if daykey in countdown.fired:
            return
        countdown.fired.add(daykey)
        ch = find_channel(guild, 'general-chat')
        if not ch:
            return
        hh = marker[1] % 12 if marker[1] % 12 else 12
        label = f'{hh} {"AM" if marker[1] < 12 else "PM"} ET'
        state = await asyncio.to_thread(get_state)
        # PERSISTED dedupe (survives restarts + blocks duplicate replicas):
        fired = state.get('count_fired', [])
        if daykey in fired:
            return
        today = f'{now.tm_year}{now.tm_mon:02d}{now.tm_mday:02d}'
        yest = (datetime.date(now.tm_year, now.tm_mon, now.tm_mday) - datetime.timedelta(days=1)).strftime('%Y%m%d')
        state['count_fired'] = [k for k in fired + [daykey] if k.startswith(today) or k.startswith(yest)]
        rid = state.get('scan_role_id')
        # SPAM LAW: exactly ONE notification per scan — T-10 pings the role, T-60 is text-only hype
        mention = f'<@&{rid}> ' if (rid and marker[0] == '10') else ''
        if marker[0] == '60':
            pool = COUNT_60
            idx = state.get('count60_idx', 0)
            state['count60_idx'] = idx + 1
        else:
            pool = COUNT_10
            idx = state.get('count10_idx', 0)
            state['count10_idx'] = idx + 1
        await asyncio.to_thread(gh_put, 'bot_state.json', state, 'countdown rotation')
        await ch.send(mention + pool[idx % len(pool)].format(label=label))
    except Exception as e:
        print('countdown error:', e)
countdown.fired = set()

PICK_ODDS = re.compile(r'[-+]\d{3}')
UNITS_PAT = re.compile(r'\b\d+(\.\d+)?u\b')
TIMEDATE_PAT = re.compile(r'(\b\d{1,2}(:\d{2})?\s?(AM|PM|am|pm)\b)|(\bET\b|EST|EDT)|tonight|today|tomorrow|(\b\d{1,2}/\d{1,2}\b)', re.I)
PICK_CHANNELS = ('free-pick', 'daily-locks', 'all-picks', 'every-play', '100-to-1000')

@tasks.loop(seconds=1800)
async def audit():
    try:
        if not client.guilds:
            return
        guild = client.guilds[0]
        flags = []
        for ch in guild.text_channels:
            if not any(k in ch.name for k in PICK_CHANNELS):
                continue
            try:
                async for m in ch.history(limit=15):
                    if client.user and m.author.id == client.user.id:
                        continue
                    txt = m.content or ''
                    if PICK_ODDS.search(txt) and UNITS_PAT.search(txt) and not TIMEDATE_PAT.search(txt):
                        flags.append(f'#{ch.name} | msg {m.id} | {txt[:90]}')
            except Exception:
                pass
        pulse = {}
        for ch in guild.text_channels:
            if not any(k in ch.name for k in PICK_CHANNELS + ('receipts', 'giveaway', 'general-chat', 'promotions')):
                continue
            try:
                msgs = [m async for m in ch.history(limit=1)]
                if msgs:
                    pulse[ch.name] = msgs[0].created_at.strftime('%Y-%m-%d %H:%M UTC')
            except Exception:
                pass
        # --- resolution watch: every pick registered in picks.json must settle into receipts
        now_ts = time.time()
        res_flags = []
        picks_doc = await asyncio.to_thread(gh_get_json_ref, 'picks.json', 'main')
        for p in (picks_doc.get('picks') or []):
            try:
                if str(p.get('result', '')).upper() not in ('', 'PENDING', 'NONE', 'NULL'):
                    continue
                gt = pick_game_utc(p.get('date', ''), p.get('time_et'))
                if gt and now_ts - gt > 4 * 3600:
                    res_flags.append(f"{p.get('id')} | {p.get('desc')} {p.get('odds')} | tier={p.get('tier')}")
            except Exception:
                pass
        # --- challenge watch: a bet must be registered daily by 6 PM ET
        chal_flags = []
        chal = await asyncio.to_thread(gh_get_json_ref, 'challenge.json', 'main')
        try:
            now_et = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=4)
            today_et = now_et.strftime('%Y-%m-%d')
            plays = chal.get('plays') or []
            if now_et.hour >= 18 and not any(pl.get('date') == today_et for pl in plays):
                chal_flags.append(f'no challenge bet registered for {today_et} (due 6 PM ET)')
            for pl in plays:
                if pl.get('result') in (None, ''):
                    gt = pick_game_utc(pl.get('date', ''), pl.get('time_et'))
                    if gt and now_ts - gt > 4 * 3600:
                        chal_flags.append(f"challenge bet #{pl.get('n')} unsettled: {pl.get('pick')}")
        except Exception:
            pass
        # --- giveaway watch: Sunday 6 PM ET draw must be posted
        gw_flags = []
        try:
            now_et2 = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=4)
            if now_et2.strftime('%a') == 'Sun' and now_et2.hour >= 19:
                gwp = pulse.get('\U0001F381giveaway', '')
                if not gwp.startswith(now_et2.strftime('%Y-%m-%d')):
                    gw_flags.append('giveaway: no winner post today (Sunday draw overdue)')
        except Exception:
            pass
        state = await asyncio.to_thread(get_state)
        if state is not None:
            state['time_audit'] = {'at': time.strftime('%Y-%m-%d %H:%M UTC'), 'flags': flags[:10]}
            state['room_pulse'] = pulse
            state['resolution_watch'] = {'at': time.strftime('%Y-%m-%d %H:%M UTC'), 'flags': res_flags[:12]}
            state['challenge_watch'] = {'at': time.strftime('%Y-%m-%d %H:%M UTC'), 'flags': chal_flags[:6]}
            state['giveaway_watch'] = {'at': time.strftime('%Y-%m-%d %H:%M UTC'), 'flags': gw_flags[:4]}
            state['bot_version'] = '8.9.37'
            try:
                await asyncio.to_thread(gh_put, 'bot_state.json', state, 'audit update')
            except Exception:
                pass
        print(f'audit: time={len(flags)} res={len(res_flags)} chal={len(chal_flags)} gw={len(gw_flags)}')
    except Exception as e:
        print('audit error:', e)

# ---------- AUTO-GRADER (v8.3): event-driven results, not clock-driven ----------
ESPN = {'MLB': 'baseball/mlb', 'NBA': 'basketball/nba', 'WNBA': 'basketball/wnba',
        'NHL': 'hockey/nhl', 'NFL': 'football/nfl'}
TIER_BADGE = {'lock': '🔒 LOCK ROOM', 'sharp': '📊 SHARP ROOM', 'whale': '🐋 WHALE ROOM',
              'free': '🆓 FREE PICK', 'challenge': '💵 CHALLENGE'}

def norm_txt(s):
    return re.sub(r'[^a-z]', '', (s or '').lower())

def team_tokens(s):
    return re.findall(r'[a-z]+', (s or '').lower())

def side_in_desc(team_field, desc):
    # match by full name OR nickname (last token) as a WHOLE token in desc
    if not team_field:
        return False
    if norm_txt(team_field) in norm_txt(desc):
        return True
    toks = team_tokens(team_field)
    nick = toks[-1] if toks else ''
    return bool(nick) and nick in set(team_tokens(desc))

def espn_fetch(sport, ymd):
    url = f'https://site.api.espn.com/apis/site/v2/sports/{ESPN[sport]}/scoreboard?dates={ymd}'
    req = urllib.request.Request(url, headers={'User-Agent': 'lineshift-bot'})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)

def find_event(sb, away, home, prefer_ts=None):
    na, nh = norm_txt(away), norm_txt(home)
    best = None
    for ev in sb.get('events', []):
        try:
            comp = ev['competitions'][0]
            teams = {c['homeAway']: c for c in comp['competitors']}
            if na and na in norm_txt(teams['away']['team'].get('displayName', '')) \
               and nh and nh in norm_txt(teams['home']['team'].get('displayName', '')):
                completed = bool(comp.get('status', {}).get('type', {}).get('completed'))
                if prefer_ts is None:
                    return teams, completed
                try:
                    start = time.mktime(time.strptime(ev['date'][:19], '%Y-%m-%dT%H:%M:%S'))
                except Exception:
                    start = prefer_ts
                d = abs(start - prefer_ts)
                if best is None or d < best[0]:
                    best = (d, teams, completed)
        except Exception:
            continue
    if best is not None:
        return best[1], best[2]
    return None, False

def pick_start_ts(p):
    try:
        d = p.get('date', '')
        t = (p.get('time_et') or '').strip().upper()
        m = re.match(r'^(\d{1,2}):(\d{2})\s*(AM|PM)$', t)
        if not d or not m:
            return None
        hh = int(m.group(1)) % 12 + (12 if m.group(3) == 'PM' else 0)
        y, mo, dd = int(d[:4]), int(d[5:7]), int(d[8:10])
        import calendar
        return calendar.timegm((y, mo, dd, hh, int(m.group(2)), 0)) + 4 * 3600
    except Exception:
        return None

def profit_units(odds, units):
    o = float(odds)
    return units * (o / 100.0 if o > 0 else 100.0 / abs(o))

def grade_pick(p, away_s, home_s):
    desc = (p.get('desc') or '').lower()
    if (p.get('market') or '').lower() == 'total' or 'over' in desc or 'under' in desc:
        m = re.search(r'(over|under)\s*(\d+(\.\d+)?)', desc)
        if not m:
            return None
        line, tot = float(m.group(2)), away_s + home_s
        if tot == line:
            return 'PUSH', 0.0
        won = (m.group(1) == 'over') == (tot > line)
        u = float(p['units']) if p.get('units') is not None else 1.0
        return ('WIN' if won else 'LOSS'), (profit_units(p['odds'], u) if won else -u)
    side = None
    if side_in_desc(p.get('homeTeam'), desc):
        side = 'home'
    elif side_in_desc(p.get('awayTeam'), desc):
        side = 'away'
    if not side:
        return None
    won = (home_s > away_s) if side == 'home' else (away_s > home_s)
    u = float(p['units']) if p.get('units') is not None else 1.0
    return ('WIN' if won else 'LOSS'), (profit_units(p['odds'], u) if won else -u)

XKEY = os.environ.get('X_SCHEDULER_KEY', '')

def x_key_load():
    if XKEY:
        return XKEY
    try:
        d = gh_get('x_key.txt', ref=QUEUE_BRANCH)
        return base64.b64decode(d['content']).decode().strip()
    except Exception:
        return ''

def x_creds_load():
    try:
        d = gh_get('x_creds.json', ref=QUEUE_BRANCH)
        return json.loads(base64.b64decode(d['content']).decode())
    except Exception:
        return {}

X_REDIRECT = 'https://thelineshift.com'

def x_oauth2_refresh(c):
    import urllib.parse
    data = {'grant_type': 'refresh_token', 'refresh_token': c['oauth2_refresh'], 'client_id': c['client_id']}
    basic = base64.b64encode(f"{c['client_id']}:{c['client_secret']}".encode()).decode()
    req = urllib.request.Request('https://api.x.com/2/oauth2/token',
                                 data=urllib.parse.urlencode(data).encode(), method='POST',
                                 headers={'Content-Type': 'application/x-www-form-urlencoded',
                                          'Authorization': f'Basic {basic}'})
    with urllib.request.urlopen(req, timeout=20) as r:
        t = json.load(r)
    c['oauth2_access'] = t['access_token']
    c['oauth2_refresh'] = t.get('refresh_token', c['oauth2_refresh'])
    c['oauth2_expires_at'] = time.time() + t.get('expires_in', 7200) - 120
    gh_put('x_creds.json', c, 'oauth2 user token refresh')
    return c

def x_post_native(text):
    c = x_creds_load()
    if c.get('oauth2_access'):
        if time.time() > c.get('oauth2_expires_at', 0):
            c = x_oauth2_refresh(c)
        req = urllib.request.Request('https://api.x.com/2/tweets',
                                     data=json.dumps({'text': text}).encode(), method='POST',
                                     headers={'Authorization': f"Bearer {c['oauth2_access']}",
                                              'Content-Type': 'application/json', 'User-Agent': 'TheLineShift/1.0'})
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            raise Exception(f'HTTP {e.code}: {e.read()[:300]}')
    return x_post_oauth1(text)

def x_post_oauth1(text):
    import hmac, hashlib, secrets, urllib.parse
    c = x_creds_load()
    if not all(c.get(k) for k in ('api_key', 'api_secret', 'access_token', 'access_token_secret')):
        return None
    url = 'https://api.x.com/2/tweets'
    op = {'oauth_consumer_key': c['api_key'], 'oauth_nonce': secrets.token_hex(16),
          'oauth_signature_method': 'HMAC-SHA1', 'oauth_timestamp': str(int(time.time())),
          'oauth_token': c['access_token'], 'oauth_version': '1.0'}
    q = lambda s: urllib.parse.quote(str(s), safe='')
    base = '&'.join(['POST', q(url), q('&'.join(f'{q(k)}={q(v)}' for k, v in sorted(op.items())))])
    key = f"{q(c['api_secret'])}&{q(c['access_token_secret'])}"
    op['oauth_signature'] = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    hdr = 'OAuth ' + ', '.join(f'{k}="{q(v)}"' for k, v in sorted(op.items()))
    req = urllib.request.Request(url, data=json.dumps({'text': text}).encode(), method='POST',
                                 headers={'Authorization': hdr, 'Content-Type': 'application/json',
                                          'User-Agent': 'TheLineShift/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise Exception(f'HTTP {e.code}: {e.read()[:300]}')


def x_oauth1_sign(method, url, ck, cs, at, ats):
    import hmac, hashlib, secrets, urllib.parse
    op = {'oauth_consumer_key': ck, 'oauth_nonce': secrets.token_hex(16),
          'oauth_signature_method': 'HMAC-SHA1', 'oauth_timestamp': str(int(time.time())),
          'oauth_version': '1.0'}
    if at:
        op['oauth_token'] = at
    q = lambda s: urllib.parse.quote(str(s), safe='')
    base = '&'.join([method.upper(), q(url), q('&'.join(f'{q(k)}={q(v)}' for k, v in sorted(op.items())))])
    key = f"{q(cs)}&{q(ats or '')}"
    op['oauth_signature'] = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    return 'OAuth ' + ', '.join(f'{k}="{q(v)}"' for k, v in sorted(op.items()))

def x_oauth1_sets(c):
    sets = []
    o1 = c.get('oauth1') or {}
    if all(o1.get(k) for k in ('consumer_key', 'consumer_secret', 'access_token', 'access_token_secret')):
        sets.append(('app2', o1['consumer_key'], o1['consumer_secret'], o1['access_token'], o1['access_token_secret']))
    if all(c.get(k) for k in ('api_key', 'api_secret', 'access_token', 'access_token_secret')):
        sets.append(('legacy', c['api_key'], c['api_secret'], c['access_token'], c['access_token_secret']))
    return sets

def x_upload_media_oauth1(img, filename='image.png'):
    import secrets
    c = x_creds_load()
    url = 'https://api.x.com/2/media/upload'
    # path 1: OAuth2 user-context (works when token carries media.write scope)
    if c.get('oauth2_access'):
        try:
            if time.time() > c.get('oauth2_expires_at', 0):
                c = x_oauth2_refresh(c)
            boundary = '----shift' + secrets.token_hex(8)
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="media_category"\r\n\r\ntweet_image\r\n'
                    f'--{boundary}\r\nContent-Disposition: form-data; name="media"; filename="{filename}"\r\n'
                    f'Content-Type: image/png\r\n\r\n').encode() + img + f'\r\n--{boundary}--\r\n'.encode()
            req = urllib.request.Request(url, data=body, method='POST',
                headers={'Authorization': f"Bearer {c['oauth2_access']}",
                         'Content-Type': f'multipart/form-data; boundary={boundary}',
                         'User-Agent': 'TheLineShift/1.0'})
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.load(r)
            mid = d.get('data', {}).get('id') or d.get('media_id_string') or d.get('media_id')
            if mid:
                return 'oauth2', str(mid)
        except urllib.error.HTTPError as e:
            try:
                eb = e.read()[:250]
            except Exception:
                eb = b''
            print(f'oauth2 upload path failed: HTTP {e.code}: {eb}')
        except Exception as e:
            print('oauth2 upload path failed:', e)
    sets = x_oauth1_sets(c)
    if not sets:
        raise Exception('no working media credential (oauth2 rejected, no complete oauth1 set)')
    errs = []
    last = None
    for name, ck, cs, at, ats in sets:
        try:
            boundary = '----shift' + secrets.token_hex(8)
            hdr = x_oauth1_sign('POST', url, ck, cs, at, ats)
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="media_category"\r\n\r\ntweet_image\r\n'
                    f'--{boundary}\r\nContent-Disposition: form-data; name="media"; filename="{filename}"\r\n'
                    f'Content-Type: image/png\r\n\r\n').encode() + img + f'\r\n--{boundary}--\r\n'.encode()
            req = urllib.request.Request(url, data=body, method='POST',
                headers={'Authorization': hdr,
                         'Content-Type': f'multipart/form-data; boundary={boundary}',
                         'User-Agent': 'TheLineShift/1.0'})
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.load(r)
            mid = d.get('data', {}).get('id') or d.get('media_id_string') or d.get('media_id')
            if not mid:
                raise Exception(f'no media id in {str(d)[:200]}')
            return name, str(mid)
        except urllib.error.HTTPError as e:
            try:
                eb = e.read()[:250]
            except Exception:
                eb = b''
            last = f'{name} HTTP {e.code}: {eb}'
        except Exception as e:
            last = f'{name}: {e}'
        errs.append(str(last))
    raise Exception(' || '.join(errs) if errs else 'upload failed')

def x_post_media_oauth2(text, media_id):
    c = x_creds_load()
    if time.time() > c.get('oauth2_expires_at', 0):
        c = x_oauth2_refresh(c)
    payload = json.dumps({'text': text, 'media': {'media_ids': [str(media_id)]}}).encode()
    req = urllib.request.Request('https://api.x.com/2/tweets', data=payload, method='POST',
        headers={'Authorization': f"Bearer {c['oauth2_access']}", 'Content-Type': 'application/json',
                 'User-Agent': 'TheLineShift/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise Exception(f'HTTP {e.code}: {e.read()[:300]}')

def x_post_media_oauth1(text, media_id, cred_name=None):
    c = x_creds_load()
    sets = x_oauth1_sets(c)
    if cred_name:
        sets = [s for s in sets if s[0] == cred_name] or sets
    url = 'https://api.x.com/2/tweets'
    payload = json.dumps({'text': text, 'media': {'media_ids': [str(media_id)]}}).encode()
    last = None
    for name, ck, cs, at, ats in sets:
        try:
            hdr = x_oauth1_sign('POST', url, ck, cs, at, ats)
            req = urllib.request.Request(url, data=payload, method='POST',
                headers={'Authorization': hdr, 'Content-Type': 'application/json', 'User-Agent': 'TheLineShift/1.0'})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            try:
                eb = e.read()[:250]
            except Exception:
                eb = b''
            last = f'{name} HTTP {e.code}: {eb}'
        except Exception as e:
            last = f'{name}: {e}'
    raise Exception(last or 'media tweet failed')

def x_post(text):
    try:
        res = x_post_native(text)
        if res:
            return res
    except Exception as e:
        print('native X post failed:', e)
    key = x_key_load()
    if not key:
        return None
    body = {"platforms": {"x": {"enabled": True, "posts": [{"text": text}]}}, "publish_at": "now"}
    req = urllib.request.Request("https://api.typefully.com/v2/social-sets/321722/drafts",
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)

def tier_season_line(all_picks, key):
    season = [p for p in all_picks if p.get('result') in ('WIN', 'LOSS', 'PUSH')
              and str(p.get('date', '')).startswith('2026') and p.get('tier') == key]
    w = sum(1 for p in season if p['result'] == 'WIN')
    l = sum(1 for p in season if p['result'] == 'LOSS')
    u = sum(units_of(p) for p in season)
    return w, l, u

CLOSERS_WIN = [
    "Posted before first pitch, graded in public. 👆",
    "Green before first pitch, green on the timeline.",
    "Another one stamped. Receipts stay up forever.",
    "Called it, posted it, cashed it.",
    "The model saw it early. The timeline proves it.",
    "Winners hit different when you post them in advance.",
    "Clockwork. On to the next edge.",
]
CLOSERS_LOSS = [
    "We show every single one — that's why the wins mean something. 👆",
    "Losses stay up too. Always have.",
    "Red on the board, posted anyway. Full ledger, always.",
    "No deletes here. Next edge already loading.",
    "That one hurt. It's staying up anyway.",
    "Public picks, public losses. That's the deal.",
]
CLOSERS_PUSH = [
    "Every result posted, always. Link in bio 👆",
    "Stake back, board moves on.",
    "Push. Nothing lost, nothing hidden.",
]

def _pick_closer(pool, seed):
    import hashlib as _hh
    return pool[int(_hh.md5(str(seed).encode()).hexdigest(), 16) % len(pool)]

def x_receipt_text(r, all_picks=None, chal=None):
    odds = r.get('odds'); odds_s = f"({odds:+d})" if isinstance(odds, int) else f"({odds})"
    badge = TIER_BADGE.get(r.get('tier'), '')
    rec_lines = []
    if all_picks is not None and r.get('tier') != 'challenge':
        tw, tl, tu = tier_season_line(all_picks, r.get('tier'))
        sw, sl, sp, su, _, _ = season_block(all_picks)
        rec_lines.append(f"{badge.split()[0]} season {tw}-{tl} ({'+' if tu >= 0 else ''}{tu:.1f}u) · 📅 overall {sw}-{sl} ({'+' if su >= 0 else ''}{su:.1f}u)")
    if r.get('tier') == 'challenge' and chal:
        rec = chal.get('record', {})
        rec_lines.append(f"💵 bankroll ${chal.get('balance', 0):.2f} ({rec.get('wins', 0)}-{rec.get('losses', 0)}) · goal $1,000")
    rec_block = ('\n' + '\n'.join(rec_lines) + '\n') if rec_lines else ''
    seed = f"{r.get('id')}{r.get('date')}{r.get('result')}"
    if r['result'] == 'WIN':
        return (f"🧾 RESULT {badge}: {r['desc']} {odds_s} ✅ +{r.get('units')}u\n{r.get('score')}\n{rec_block}\n"
                + _pick_closer(CLOSERS_WIN, seed))
    if r['result'] == 'PUSH':
        return (f"🧾 RESULT {badge}: {r['desc']} {odds_s} 🟰 PUSH — stake back.\n{r.get('score')}\n{rec_block}\n"
                + _pick_closer(CLOSERS_PUSH, seed))
    return (f"🧾 RESULT {badge}: {r['desc']} {odds_s} ❌ {r.get('units')}u\n{r.get('score')}\n{rec_block}\n"
            + _pick_closer(CLOSERS_LOSS, seed))

async def settle_challenge(guild, p):
    try:
        chal = await asyncio.to_thread(gh_get_json_ref, 'challenge.json', 'main')
        hit = None
        for pl in chal.get('plays', []):
            if pl.get('result') in (None, '') and pl.get('date') == p.get('date') \
               and norm_txt(pl.get('pick')) and norm_txt(pl['pick']) in norm_txt(p.get('desc')):
                hit = pl
                break
        if not hit:
            return
        hit['result'] = p['result']
        if p['result'] == 'WIN':
            chal['balance'] = round(chal.get('balance', 100) + float(hit.get('toWin', 0)), 2)
            chal['record']['wins'] += 1
        elif p['result'] == 'LOSS':
            chal['balance'] = round(chal.get('balance', 100) - float(hit.get('stake', 0)), 2)
            chal['record']['losses'] += 1
        chal['updated'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        await asyncio.to_thread(gh_put, 'challenge.json', chal, f"settle bet #{hit.get('n')}: {p['result']}", 'main')
        ch = find_channel(guild, '100-to-1000')
        if ch:
            e = '✅' if p['result'] == 'WIN' else ('🟰' if p['result'] == 'PUSH' else '❌')
            nxt = min(chal['balance'] * 0.2, chal['balance'])
            await ch.send(f"💵 **CHALLENGE BET #{hit.get('n')} — {p['result']}** {e}\n"
                          f"{p.get('desc')} ({p.get('odds')}) · Final: {p.get('score')}\n"
                          f"**BALANCE: ${chal['balance']:.2f}** (goal: ${chal.get('goal', 1000):.0f}) · record {chal['record']['wins']}-{chal['record']['losses']}\n"
                          f"BET #{hit.get('n') + 1} drops with tomorrow's card. — SHiFT 🤖")
    except Exception as e:
        print('settle_challenge error:', e)

@tasks.loop(seconds=1200)
async def grader():
    try:
        if not client.guilds:
            return
        doc = await asyncio.to_thread(gh_get_json_ref, 'picks.json', 'main')
        new_results = []
        for p in (doc.get('picks') or []):
            try:
                if str(p.get('result', '')).upper() not in ('', 'PENDING', 'NONE', 'NULL'):
                    continue
                sport = (p.get('sport') or '').upper()
                if sport not in ESPN:
                    continue  # tennis/esports/etc -> scan-engine research path
                gt = pick_game_utc(p.get('date', ''), p.get('time_et'))
                if not gt or time.time() < gt + 5400:
                    continue  # earliest a final is possible
                sb = await asyncio.to_thread(espn_fetch, sport, p['date'].replace('-', ''))
                teams, ev_completed = find_event(sb, p.get('awayTeam'), p.get('homeTeam'), pick_start_ts(p))
                if not teams or not ev_completed:
                    continue
                away_s = int(float(teams['away'].get('score') or 0))
                home_s = int(float(teams['home'].get('score') or 0))
                g = grade_pick(p, away_s, home_s)
                if not g:
                    continue
                p['result'], u = g
                p['score'] = f"{p.get('awayTeam')} {away_s}, {p.get('homeTeam')} {home_s}"
                p['units_result'] = round(u, 2)
                new_results.append(p)
            except Exception:
                continue
        if not new_results:
            return
        doc['updated'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        await asyncio.to_thread(gh_put, 'picks.json', doc,
                                'auto-grade: ' + ', '.join(p['id'] for p in new_results), 'main')
        guild = client.guilds[0]
        ch = find_channel(guild, 'receipts')
        state = await asyncio.to_thread(get_state)
        for p in new_results:
            e = '✅' if p['result'] == 'WIN' else ('🟰' if p['result'] == 'PUSH' else '❌')
            u = p.get('units_result', 0)
            us = f'+{u}u' if u > 0 else f'{u}u'
            overnight = ''
            gt = pick_game_utc(p.get('date', ''), p.get('time_et'))
            if gt and (time.gmtime(gt).tm_hour - 4) % 24 < 6:
                overnight = '\n📅 counts for tomorrow\'s card'
            badge = TIER_BADGE.get(p.get('tier'), '')
            if ch:
                await ch.send(f"🧾 **RESULT {badge}:** {p.get('desc')} ({p.get('odds')}) {e} **{p['result']}** {us}\n"
                              f"Final: {p.get('score')}{overnight}")
            if p.get('tier') == 'challenge':
                await settle_challenge(guild, p)
            if state is not None:
                state.setdefault('unannounced_results', []).append(
                    {'id': p['id'], 'desc': p.get('desc'), 'odds': p.get('odds'), 'result': p['result'],
                     'units': p.get('units_result'), 'score': p.get('score'), 'tier': p.get('tier')})
        if state is not None:
            try:
                await asyncio.to_thread(gh_put, 'bot_state.json', state, 'grader results')
            except Exception:
                pass
        print(f'grader: {len(new_results)} result(s) posted')
        # X drain happens in its own paced block below
    except Exception as e:
        print('grader error:', e)

@tasks.loop(seconds=1200)
async def x_drainer():
    # posts queued results to X — max 1 per cycle, >=40 min between X receipts (pacing rule)
    try:
        state = await asyncio.to_thread(get_state)
        if state is None:
            return
        queue = state.get('unannounced_results') or []
        if not queue:
            return
        last = state.get('last_x_receipt_ts', 0)
        if time.time() - float(last) < 40 * 60:
            return
        r = queue[0]
        picks_doc = await asyncio.to_thread(gh_get_json_ref, 'picks.json', 'main')
        chal_doc = await asyncio.to_thread(gh_get_json_ref, 'challenge.json', 'main') if r.get('tier') == 'challenge' else None
        resp = await asyncio.to_thread(x_post, x_receipt_text(r, picks_doc.get('picks'), chal_doc))
        if resp is None:
            print('x_drainer: no X key available')
            return
        state['unannounced_results'] = queue[1:]
        state['last_x_receipt_ts'] = time.time()
        await asyncio.to_thread(gh_put, 'bot_state.json', state, f"x receipt posted: {r.get('id')}")
        print(f"x_drainer: posted {r.get('id')}, {len(queue) - 1} left")
    except Exception as e:
        print('x_drainer error:', e)

def units_of(p):
    if p.get('units_result') is not None:
        return float(p['units_result'])
    u = float(p['units']) if p.get('units') is not None else 1.0
    if p.get('result') == 'WIN':
        return profit_units(p.get('odds', -110), u)
    if p.get('result') == 'LOSS':
        return -u
    return 0.0

def season_block(all_picks):
    # per-tier season records for the 4 rooms; overall = SUM of the rooms by construction.
    # challenge is reported separately (it often mirrors a room pick - never double-counted).
    tiers = [('lock', '🔒'), ('sharp', '📊'), ('whale', '🐋'), ('free', '🆓')]
    season = [p for p in all_picks if p.get('result') in ('WIN', 'LOSS', 'PUSH')
              and str(p.get('date', '')).startswith('2026')]
    parts, tot_w, tot_l, tot_p, tot_u = [], 0, 0, 0, 0.0
    for key, badge in tiers:
        tp = [p for p in season if p.get('tier') == key]
        if not tp:
            continue
        w = sum(1 for p in tp if p['result'] == 'WIN')
        l = sum(1 for p in tp if p['result'] == 'LOSS')
        pu = sum(1 for p in tp if p['result'] == 'PUSH')
        u = sum(units_of(p) for p in tp)
        tot_w += w; tot_l += l; tot_p += pu; tot_u += u
        rec = f"{w}-{l}" + (f"-{pu}" if pu else "")
        parts.append(f"{badge} {rec} ({'+' if u >= 0 else ''}{u:.2f}u)")
    chal = [p for p in season if p.get('tier') == 'challenge']
    cw = sum(1 for p in chal if p['result'] == 'WIN')
    cl = sum(1 for p in chal if p['result'] == 'LOSS')
    return tot_w, tot_l, tot_p, tot_u, ' · '.join(parts), (cw, cl)

@tasks.loop(seconds=900)
async def recap_watch():
    # server-side nightly recap: posts when every today-starting (ET) game is settled. Never missed.
    try:
        if not client.guilds:
            return
        state = await asyncio.to_thread(get_state)
        if state is None:
            return
        now_et = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=4)
        if 6 <= now_et.hour < 21:
            return  # recap window is 9 PM - 6 AM ET
        recap_date = now_et.strftime('%Y-%m-%d') if now_et.hour >= 21 else (now_et - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        if state.get('last_recap_date') == recap_date:
            return
        doc = await asyncio.to_thread(gh_get_json_ref, 'picks.json', 'main')
        all_picks = doc.get('picks') or []
        day = [p for p in all_picks if p.get('date') == recap_date]
        if not day:
            return
        if any(str(p.get('result', '')).upper() in ('', 'PENDING', 'NONE', 'NULL') for p in day):
            return  # games still live
        settled = [p for p in day if p.get('result') in ('WIN', 'LOSS', 'PUSH')]
        if not settled:
            return
        tiers = [('lock', '🔒 LOCK ROOM'), ('sharp', '📊 SHARP'), ('whale', '🐋 WHALE'), ('free', '🆓 FREE PICK')]
        mmdd = recap_date[5:].replace('-', '/')
        lines = [f"🌙 **THELINESHIFT NIGHTLY RECAP — {mmdd}**", "(every tier, every result — graded in public)", ""]
        tot_w = tot_l = tot_p = 0
        tot_u = 0.0
        for key, label in tiers:
            tp = [p for p in settled if p.get('tier') == key]
            if not tp:
                continue
            w = sum(1 for p in tp if p['result'] == 'WIN')
            l = sum(1 for p in tp if p['result'] == 'LOSS')
            pu = sum(1 for p in tp if p['result'] == 'PUSH')
            u = sum(units_of(p) for p in tp)
            tot_w += w; tot_l += l; tot_p += pu; tot_u += u
            suffix = f"-{pu}" if pu else ""
            lines.append(f"{label} — {w}-{l}{suffix}, {'+' if u >= 0 else ''}{u:.2f}u " + ('✅' if u > 0 else '❌' if u < 0 else ''))
            for p in tp:
                e = '✅' if p['result'] == 'WIN' else ('🟰' if p['result'] == 'PUSH' else '❌')
                uu = units_of(p)
                lines.append(f"{e} {p.get('desc')} ({p.get('odds')}) → {p.get('score', 'final')} → {'+' if uu >= 0 else ''}{uu:.2f}u")
            lines.append("")
        sw, sl, sp, su, tier_split, chal_rec = season_block(all_picks)
        lines.append(f"**FULL BOARD: {tot_w}-{tot_l}" + (f"-{tot_p}" if tot_p else "") + f" ({'+' if tot_u >= 0 else ''}{tot_u:.2f}u).**")
        lines.append(f"📅 **2026 SEASON: {sw}-{sl}" + (f"-{sp}" if sp else "") + f" ({'+' if su >= 0 else ''}{su:.2f}u)**")
        lines.append(tier_split + f"  |  💵 challenge {chal_rec[0]}-{chal_rec[1]} (tracked in dollars)")
        try:
            chal = await asyncio.to_thread(gh_get_json_ref, 'challenge.json', 'main')
            rec = chal.get('record', {})
            lines.append(f"💵 Challenge: balance ${chal.get('balance', 0):.2f} ({rec.get('wins', 0)}-{rec.get('losses', 0)}) — goal $1,000")
        except Exception:
            pass
        ch = find_channel(client.guilds[0], 'receipts')
        if ch:
            await ch.send('\n'.join(lines))
        state['last_recap_date'] = recap_date
        await asyncio.to_thread(gh_put, 'bot_state.json', state, f'recap posted {recap_date}')
        try:
            xt = (f"🌙 FULL BOARD {mmdd}: {tot_w}-{tot_l} ({'+' if tot_u >= 0 else ''}{tot_u:.1f}u)\n"
                  f"📅 2026 season: {sw}-{sl} ({'+' if su >= 0 else ''}{su:.1f}u)\n"
                  f"{tier_split}\n\n"
                  f"Every pick posted early, every result graded in public. First month FREE 👆")
            await asyncio.to_thread(x_post, xt)
        except Exception as e:
            print('recap X error:', e)
        print('recap posted for', recap_date)
    except Exception as e:
        print('recap_watch error:', e)

def side_ml(p, ho, ao):
    d = (p.get('desc') or '').lower()
    if 'over' in d or 'under' in d:
        return None
    if side_in_desc(p.get('awayTeam', ''), p.get('desc', '')):
        return ao
    if side_in_desc(p.get('homeTeam', ''), p.get('desc', '')):
        return ho
    return None

def fmt_odds_num(n):
    try:
        n = int(n)
        return f'+{n}' if n > 0 else str(n)
    except Exception:
        return str(n)

def clv_note(p, ho, ao):
    cur = side_ml(p, ho, ao)
    if cur is None or p.get('odds') is None:
        return ''
    diff = int(p['odds']) - int(cur)
    if diff >= 5:
        return f'📈 CLV +{diff}c — we beat the close. That\'s the whole game.'
    if diff <= -5:
        return f'📉 CLV {diff}c — market moved against us.'
    return '➡️ closed right at our number.'

@tasks.loop(seconds=1800)
async def odds_watch():
    try:
        if not client.guilds:
            return
        doc = await asyncio.to_thread(gh_get_json_ref, 'picks.json', 'main')
        plist = doc.get('picks') or []
        today = time.strftime('%Y-%m-%d', time.gmtime(time.time() - 4 * 3600))
        pend = [p for p in plist if str(p.get('result', '')).upper() in ('', 'PENDING', 'NONE', 'NULL')
                and (p.get('sport') or '').upper() in ESPN and p.get('date') == today]
        if not pend:
            return
        guild = client.guilds[0]
        ch = find_channel(guild, 'whale-talk')
        changed = False
        for p in pend:
            try:
                sport = (p.get('sport') or '').upper()
                sb = await asyncio.to_thread(espn_fetch, sport, p['date'].replace('-', ''))
                prefer = pick_start_ts(p)
                na, nh = norm_txt(p.get('awayTeam', '')), norm_txt(p.get('homeTeam', ''))
                best = None
                for ev in sb.get('events', []):
                    try:
                        comp = ev['competitions'][0]
                        teams = {c2['homeAway']: c2 for c2 in comp['competitors']}
                        if na in norm_txt(teams['away']['team'].get('displayName', '')) and nh in norm_txt(teams['home']['team'].get('displayName', '')):
                            try:
                                start = time.mktime(time.strptime(ev['date'][:19], '%Y-%m-%dT%H:%M:%S'))
                            except Exception:
                                start = prefer or 0
                            d = abs(start - (prefer or start))
                            if best is None or d < best[0]:
                                best = (d, comp)
                    except Exception:
                        continue
                if not best:
                    continue
                comp = best[1]
                odds = (comp.get('odds') or [{}])[0]
                ho = (odds.get('homeTeamOdds') or {}).get('moneyLine')
                ao = (odds.get('awayTeamOdds') or {}).get('moneyLine')
                ou = odds.get('overUnder')
                if ho is None and ao is None and ou is None:
                    continue
                p['live_odds'] = {'home_ml': ho, 'away_ml': ao, 'total': ou, 'ts': int(time.time())}
                changed = True
                stype = comp.get('status', {}).get('type', {})
                started = stype.get('state') == 'in' or bool(stype.get('completed'))
                if started and not p.get('closing_odds'):
                    p['closing_odds'] = dict(p['live_odds'])
                    if ch:
                        await ch.send(f"🔒 **CLOSING LINE LOCKED** — {p.get('desc')}: we took {fmt_odds_num(p.get('odds'))}, closing {fmt_odds_num(side_ml(p, ho, ao)) if side_ml(p, ho, ao) is not None else 'total ' + str(ou)}. {clv_note(p, ho, ao)}")
                elif not started:
                    cur = side_ml(p, ho, ao)
                    posted = p.get('odds')
                    if cur is not None and posted is not None:
                        anchor_o = p.get('last_alert_odds', posted)
                        if abs(int(cur) - int(anchor_o)) >= 12 and ch:
                            p['last_alert_odds'] = int(cur)
                            verdict = 'we got the best of it ✅' if int(cur) < int(posted) else 'market moving against us 👀'
                            await ch.send(f"⚠️ **LINE MOVE** — {p.get('desc')}: {fmt_odds_num(anchor_o)} → {fmt_odds_num(cur)}. Steam on this one — {verdict}")
                    elif ou is not None and p.get('market') == 'total':
                        try:
                            posted_t = float(re.search(r'(\d+(\.\d+)?)', p.get('desc', '')).group(1))
                            anchor_t = p.get('last_alert_total', posted_t)
                            if abs(float(ou) - anchor_t) >= 0.5 and ch:
                                p['last_alert_total'] = float(ou)
                                await ch.send(f"⚠️ **TOTAL MOVE** — {p.get('desc')}: {anchor_t} → {ou}. {'Money pounding the over.' if float(ou) > anchor_t else 'Steam on the under.'}")
                        except Exception:
                            pass
            except Exception as e:
                print('odds_watch pick error:', e)
        if changed:
            await asyncio.to_thread(gh_put, 'picks.json', doc, 'odds watch update', 'main')
    except Exception as e:
        print('odds_watch error:', e)

@tasks.loop(seconds=3600)
async def teaser_watch():
    try:
        if not client.guilds:
            return
        guild = client.guilds[0]
        now = time.gmtime()
        if now.tm_hour != 12:
            return
        state = await asyncio.to_thread(get_state)
        tz = state.setdefault('teasers', {})
        import datetime as _dt
        today = _dt.date(now.tm_year, now.tm_mon, now.tm_mday)
        next_sun = today + _dt.timedelta(days=(6 - today.weekday()) % 7)
        ws = next_sun.isoformat()
        if tz.get('weekly') != ws:
            ch = find_channel(guild, 'weekly-analytics')
            if ch:
                await ch.send(f"📊 **WEEKLY ANALYTICS — next report: Sunday {next_sun.strftime('%b %d')}, 10:00 AM ET**\nFull-board review: tier-by-tier records, units chart, best/worst reads of the week, and what changes next week. 🎯")
                tz['weekly'] = ws
        nm = _dt.date(today.year + (1 if today.month == 12 else 0), 1 if today.month == 12 else today.month + 1, 1)
        ms = nm.isoformat()
        if tz.get('monthly') != ms:
            ch = find_channel(guild, 'monthly-deepdive')
            if ch:
                await ch.send(f"🐋 **MONTHLY DEEP-DIVE — next report: {nm.strftime('%b %d')}, 6:00 PM ET**\nWhale-tier masterclass: full-month model autopsy, where the edge came from, bankroll math, and next month's attack plan.")
                tz['monthly'] = ms
        await asyncio.to_thread(gh_put, 'bot_state.json', state, 'teaser check')
    except Exception as e:
        print('teaser_watch error:', e)

COUNT_60 = [
 "\u23F3 **SCAN IN 60 MINUTES** — the machine goes to work at {label}. Odds across the market, injury reports, confirmed lineups — everything gets pulled. \U0001F6F0\uFE0F",
 "\u23F3 **T-60 TO SCAN** — next sweep at {label}. The board gets stripped down to the edges worth firing on. \U0001F4E1",
 "\U0001F6F0\uFE0F **ONE HOUR OUT** — the {label} scan is loading. Six windows a day, zero guesswork.",
 "\u23F3 **60-MINUTE WARNING** — the {label} sweep is next. Data first, picks after. \U0001F916",
 "\U0001F6F0\uFE0F **SCAN APPROACHING** — {label}. The machine reads the whole board so you don't have to.",
 "\u23F3 **NEXT SCAN: {label}** — one hour. Markets, lineups, weather, money flow. Watch it work. \U0001F4CA",
]
COUNT_10 = [
 "\U0001F6F0\uFE0F **SCAN IN 10 MINUTES** — {label}. Sharpen up. \U0001F525",
 "\u26A1 **T-10** — the {label} sweep is imminent. The free pick lands with the finale. \U0001F3AF",
 "\U0001F6F0\uFE0F **10 MINUTES OUT** — {label}. The machine is warming up.",
 "\U0001F3AF **T-10 TO SCAN** — {label}. Parameters loading...",
 "\u23F1\uFE0F **FINAL 10** — the {label} sweep opens the board in minutes.",
 "\U0001F52D **SCAN IMMINENT** — {label}. Watch the machine work. \U0001F6F0\uFE0F",
]

@tasks.loop(seconds=300)
async def scan_event_watch():
    # if no scan theater in general-chat within ~25 min of an event slot, the event MISSED -> fallback post + flag
    try:
        if not client.guilds:
            return
        now = time.gmtime()
        if now.tm_hour not in EVENT_HOURS_UTC or now.tm_min < 20:
            return
        slot = f'{now.tm_year}{now.tm_mon:02d}{now.tm_mday:02d}-{now.tm_hour:02d}'
        state = await asyncio.to_thread(get_state)
        if state is None:
            return
        events = state.setdefault('scan_events', {})
        if events.get(slot):
            return
        guild = client.guilds[0]
        ch = find_channel(guild, 'general-chat')
        if not ch:
            return
        import datetime as _dt
        slot_dt = _dt.datetime(now.tm_year, now.tm_mon, now.tm_mday, now.tm_hour, 0, 0, tzinfo=_dt.timezone.utc)
        slot_ts = slot_dt.timestamp()
        fired = False
        finished = False
        passed = False
        resolved = False
        # TIME-SCOPED: only messages posted AFTER this slot started count — an old SCAN COMPLETE can never satisfy a new slot
        async for m in ch.history(limit=40, after=slot_dt):
            txt = (m.content or '')
            if any(k in txt for k in ('SCAN COMPLETE', 'SCAN INITIATED', 'ANALYZING', 'COLLECTING')):
                fired = True
            if 'SCAN COMPLETE' in txt:
                finished = True
            if 'PASSED' in txt or 'no viable' in txt.lower():
                passed = True
            if 'SCAN — RESOLUTION' in txt or 'SCAN RESOLUTION' in txt or 'discipline pass' in txt.lower() or 'slate covered' in txt.lower():
                resolved = True
        if resolved:
            # a public resolution (card / discipline pass / slate-covered) already closed this slot — never cry delay
            events[slot] = 'ok'
        elif fired and finished:
            # 'ok' REQUIRES fresh picks registered after slot start (or a deliberate discipline pass)
            fresh = False
            if not passed:
                try:
                    pj = await asyncio.to_thread(gh_get_json_ref, 'picks.json', 'main')
                    upd = pj.get('updated', '') if isinstance(pj, dict) else ''
                    upd_ts = time.mktime(time.strptime(upd[:19], '%Y-%m-%dT%H:%M:%S')) if upd else 0
                    fresh = upd_ts >= slot_ts - 300
                except Exception as e:
                    print('pick_guard fresh-check error:', e)
            if passed or fresh:
                events[slot] = 'ok'
            else:
                events[slot] = 'makeup_needed'
                await ch.send("⚠️ **PICK GUARD** — theater ran but no card registered this window. SHiFT is re-running the drop now; picks land within the hour. 🤖")
                state.setdefault('pick_guard_alerts', []).append(slot)
                print(f'pick_guard: slot {slot} theater w/o picks')
        elif fired:
            events[slot] = 'partial'
            await ch.send("⚠️ **SCAN STALLED** — collection started but never completed. SHiFT is re-running this event; card drops within the hour. 🤖")
            state.setdefault('scan_event_misses', []).append(slot)
        else:
            await ch.send("🛰️ **SCAN DELAYED** — the machine hit a snag on this run. SHiFT is catching up; the card drops shortly. 🤖")
            events[slot] = 'missed'
            state.setdefault('scan_event_misses', []).append(slot)
            print(f'scan_event_watch: slot {slot} MISSED, fallback posted')
        await asyncio.to_thread(gh_put, 'bot_state.json', state, f'scan event {slot}: {events[slot]}')
    except Exception as e:
        print('scan_event_watch error:', e)

client = make_client()
try:
    client.run(DISCORD_TOKEN)
except discord.PrivilegedIntentsRequired:
    print('PRIVILEGED INTENTS NOT ENABLED IN PORTAL - running degraded')
    client = make_client(privileged=False)
    client.run(DISCORD_TOKEN)
