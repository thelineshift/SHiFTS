import os, json, time, base64, asyncio, urllib.request, random
import discord
from discord.ext import tasks

DISCORD_TOKEN = os.environ['DISCORD_BOT_TOKEN']
GH_TOKEN = os.environ.get('GITHUB_TOKEN', '')
REPO = 'TheLineShift/AISportsBot'
RAW = f'https://raw.githubusercontent.com/{REPO}/main'
API = f'https://api.github.com/repos/{REPO}/contents'

intents = discord.Intents.default()
intents.guilds = True
client = discord.Client(intents=intents)

def gh_headers():
    return {'Authorization': f'token {GH_TOKEN}', 'User-Agent': 'lineshift-bot'}

def gh_get(path):
    req = urllib.request.Request(f'{API}/{path}', headers=gh_headers())
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)

def gh_put(path, obj, message):
    try:
        sha = gh_get(path).get('sha')
    except Exception:
        sha = None
    body = {'message': message,
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
        d = gh_get('bot_state.json')
        return json.loads(base64.b64decode(d['content']))
    except Exception:
        return {'executed_seq': 0}

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
    elif a == 'set_icon':
        req = urllib.request.Request(cmd['url'], headers={'User-Agent': 'lineshift-bot'})
        data = urllib.request.urlopen(req, timeout=25).read()
        await guild.edit(icon=data)
        log.append('server icon updated')
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
            f'{r.name} (id={r.id}, pos={r.position})'
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
            log.append(f'PIN{i} by {m.author}: {(m.content or "")[:500]}')
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
    if not GH_TOKEN:
        return
    data = await asyncio.to_thread(fetch_commands)
    if not data:
        return
    seq = data.get('seq', 0)
    state = await asyncio.to_thread(get_state)
    if seq <= state.get('executed_seq', 0):
        return
    guild = client.guilds[0] if client.guilds else None
    if not guild:
        return
    log = [f'seq {seq} executed {time.strftime("%Y-%m-%d %H:%M UTC")}']
    for cmd in data.get('commands', []):
        try:
            await run_command(cmd, guild, log)
        except Exception as e:
            log.append(f'ERROR {cmd.get("action")}: {e}')
    state['executed_seq'] = seq
    state['last_log'] = log
    try:
        await asyncio.to_thread(gh_put, 'bot_state.json', state, f'bot executed seq {seq}')
    except Exception as e:
        print('state push failed:', e)

@client.event
async def on_ready():
    print(f'LineShift Bot online as {client.user} in {len(client.guilds)} guild(s)')
    if not poll.is_running():
        poll.start()

client.run(DISCORD_TOKEN)
