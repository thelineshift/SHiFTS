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

    @c.event
    async def on_message(message):
        try:
            if message.author.bot:
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
    elif a == 'x_post_text':
        try:
            res = await asyncio.to_thread(x_post, cmd['text'])
            log.append(f'x_post_text OK: id {res.get("data", {}).get("id") if res else None}')
        except Exception as e:
            log.append(f'x_post_text FAIL: {e}')
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
                 'scope': 'tweet.read tweet.write users.read follows.read follows.write like.read like.write offline.access', 'state': pk['state'],
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
                res = await asyncio.to_thread(x_post_native, cmd.get('text', '\U0001F6F0\uFE0F SHiFT native X link online. The board never sleeps.'))
                log.append(f'x_link_finish OK: tweet id {res.get("data", {}).get("id") if res else None}')
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
        # prune: keep only today/yesterday keys so the set can't grow forever
        today = f'{now.tm_year}{now.tm_mon:02d}{now.tm_mday:02d}'
        yest = (datetime.date(now.tm_year, now.tm_mon, now.tm_mday) - datetime.timedelta(days=1)).strftime('%Y%m%d')
        countdown.fired = {k for k in countdown.fired if k.startswith(today) or k.startswith(yest)}
        ch = find_channel(guild, 'general-chat')
        if not ch:
            return
        hh = marker[1] % 12 if marker[1] % 12 else 12
        label = f'{hh} {"AM" if marker[1] < 12 else "PM"} ET'
        if marker[0] == '60':
            await ch.send(f'\u23F3 **SCAN IN 60 MINUTES** — the machine goes to work at {label}. Odds across the market, injury reports, confirmed lineups, roster moves — everything gets pulled. \U0001F6F0️')
        else:
            await ch.send(f'\U0001F6F0️ **SCAN IN 10 MINUTES** — systems hot. Picks land in your tier rooms right after. \U0001F525')
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
            state['bot_version'] = '8.9.10'
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
TIER_BADGE = {'lock': '🔒 LOCK', 'sharp': '📊 SHARP', 'whale': '🐋 WHALE',
              'free': '🆓 FREE', 'challenge': '💵 CHALLENGE'}

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

def x_receipt_text(r, all_picks=None, chal=None):
    odds = r.get('odds'); odds_s = f"({odds:+d})" if isinstance(odds, int) else f"({odds})"
    badge = TIER_BADGE.get(r.get('tier'), '')
    # records block
    rec_lines = []
    if all_picks is not None and r.get('tier') != 'challenge':
        tw, tl, tu = tier_season_line(all_picks, r.get('tier'))
        sw, sl, sp, su, _, _ = season_block(all_picks)
        rec_lines.append(f"{badge.split()[0]} season {tw}-{tl} ({'+' if tu >= 0 else ''}{tu:.1f}u) · 📅 overall {sw}-{sl} ({'+' if su >= 0 else ''}{su:.1f}u)")
    if r.get('tier') == 'challenge' and chal:
        rec = chal.get('record', {})
        rec_lines.append(f"💵 bankroll ${chal.get('balance', 0):.2f} ({rec.get('wins', 0)}-{rec.get('losses', 0)}) · goal $1,000")
    rec_block = ('\n' + '\n'.join(rec_lines) + '\n') if rec_lines else ''
    if r['result'] == 'WIN':
        return (f"🧾 {badge}: {r['desc']} {odds_s} ✅ +{r.get('units')}u\n{r.get('score')}\n{rec_block}\n"
                f"Posted before first pitch, graded in public. First month FREE 👆")
    if r['result'] == 'PUSH':
        return (f"🧾 {badge}: {r['desc']} {odds_s} 🟰 PUSH — stake back.\n{r.get('score')}\n{rec_block}\n"
                f"Every result posted, always. Link in bio 👆")
    return (f"🧾 {badge}: {r['desc']} {odds_s} ❌ {r.get('units')}u\n{r.get('score')}\n{rec_block}\n"
            f"We show every single one — that's why the wins mean something. 👆")

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
        fired = False
        finished = False
        passed = False
        async for m in ch.history(limit=14):
            txt = (m.content or '')
            if any(k in txt for k in ('SCAN COMPLETE', 'SCAN INITIATED', 'ANALYZING', 'COLLECTING')):
                fired = True
            if 'SCAN COMPLETE' in txt:
                finished = True
            if 'PASSED' in txt or 'no viable' in txt.lower():
                passed = True
        if fired and finished:
            events[slot] = 'ok'
            # PICK GUARD: theater completed — picks.json must show a fresh registration this window (unless discipline pass)
            if not passed:
                try:
                    pj = await asyncio.to_thread(gh_get_json_ref, 'picks.json', 'main')
                    upd = pj.get('updated', '') if isinstance(pj, dict) else ''
                    upd_ts = time.mktime(time.strptime(upd[:19], '%Y-%m-%dT%H:%M:%S')) if upd else 0
                    slot_ts = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, now.tm_hour, 0, 0, 0, 0, 0))
                    if upd_ts < slot_ts - 3900:
                        await ch.send("⚠️ **PICK GUARD** — theater ran but no card registered this window. SHiFT is re-running the drop now; picks land within the hour. 🤖")
                        state.setdefault('pick_guard_alerts', []).append(slot)
                        print(f'pick_guard: slot {slot} theater w/o picks')
                except Exception as e:
                    print('pick_guard error:', e)
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
