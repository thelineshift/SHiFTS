import os, json, time, base64, asyncio, urllib.request
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
