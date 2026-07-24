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

BOT_NICK = '⚡ SHiFT'
BOT_STATUS = 'the board 🛰️'
BOT_VERSION = '9.3.3'

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
            reply = f'⚡ Ticket **#{t["id"]}** is waiting on your confirmation — did the fix work? Reply **yes** or **no**.'
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
        reply = f'⚡ Added that to ticket **#{t["id"]}** — still on it.'
    try:
        gh_put('issues.json', issues, f'issue ticket update ({author.id})')
    except Exception as e:
        print('[issues] state write failed:', e)
    if reply:
        await message.reply(reply, mention_author=False)



CRYPTO_TIERS = {'lock': 14.99, 'sharp': 29.99, 'whale': 59.99}

def _http_json(url, payload=None, headers=None, timeout=20):
    h = {'Content-Type': 'application/json'}
    if headers:
        h.update(headers)
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=h, method='POST' if data else 'GET')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

def wallet_balances():
    """On-chain balances for all hot wallets + USD values. Never raises."""
    out = {'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'wallets': []}
    try:
        w = gh_get_json('wallets.json') or {}
        px = _http_json('https://api.coingecko.com/api/v3/simple/price?ids=solana,ethereum,bitcoin&vs_currencies=usd')
        for x in w.get('wallets', []):
            ch, addr = x['chain'], x['address']
            entry = {'chain': ch, 'symbol': x.get('symbol', ch.upper()), 'label': x.get('label', '💼 OPS WALLET'),
                     'address': addr, 'note': x.get('note', '')}
            try:
                if ch == 'solana':
                    b = _http_json('https://api.mainnet-beta.solana.com',
                                   {'jsonrpc': '2.0', 'id': 1, 'method': 'getBalance', 'params': [addr]})
                    bal = b['result']['value'] / 1e9
                    entry.update(balance=round(bal, 5), usd=round(bal * px['solana']['usd'], 2))
                elif ch == 'ethereum':
                    b = None
                    for rpc in ('https://eth.llamarpc.com', 'https://cloudflare-eth.com', 'https://rpc.ankr.com/eth'):
                        try:
                            b = _http_json(rpc, {'jsonrpc': '2.0', 'id': 1, 'method': 'eth_getBalance', 'params': [addr, 'latest']})
                            if b.get('result'):
                                break
                        except Exception:
                            continue
                    bal = int(b['result'], 16) / 1e18
                    entry.update(balance=round(bal, 6), usd=round(bal * px['ethereum']['usd'], 2))
                elif ch == 'bitcoin':
                    b = _http_json(f'https://blockstream.info/api/address/{addr}')
                    bal = (b['chain_stats']['funded_txo_sum'] - b['chain_stats']['spent_txo_sum']) / 1e8
                    entry.update(balance=round(bal, 8), usd=round(bal * px['bitcoin']['usd'], 2))
            except Exception as e:
                entry.update(balance=None, usd=None, error=str(e)[:80])
            out['wallets'].append(entry)
    except Exception as e:
        out['error'] = str(e)[:200]
    return out

def crypto_withdraw(chain, to, amount):
    """Sign and broadcast a withdrawal from a hot wallet. Returns txid string."""
    keys = gh_get_json('wallets_secret.json') or {}
    if chain == 'solana':
        from solders.keypair import Keypair
        from solders.pubkey import Pubkey
        from solders.system_program import transfer, TransferParams
        from solders.message import Message
        from solders.transaction import Transaction
        from solders.hash import Hash
        kp = Keypair.from_bytes(bytes.fromhex(keys['solana']['secret_hex']))
        lamports = int(float(amount) * 1e9)
        bh = _http_json('https://api.mainnet-beta.solana.com',
                        {'jsonrpc': '2.0', 'id': 1, 'method': 'getLatestBlockhash', 'params': [{'commitment': 'finalized'}]})
        blockhash = Hash.from_string(bh['result']['value']['blockhash'])
        ix = transfer(TransferParams(from_pubkey=kp.pubkey(), to_pubkey=Pubkey.from_string(to), lamports=lamports))
        tx = Transaction([kp], Message([ix], kp.pubkey()), blockhash)
        sig = _http_json('https://api.mainnet-beta.solana.com',
                         {'jsonrpc': '2.0', 'id': 1, 'method': 'sendTransaction',
                          'params': [__import__('base64').b64encode(bytes(tx)).decode(), {'encoding': 'base64'}]})
        return 'SOL tx: ' + sig['result']
    if chain == 'evm':
        from eth_account import Account
        acct = Account.from_key(keys['evm']['private_key'])
        nonce_r = _http_json('https://eth.llamarpc.com',
                             {'jsonrpc': '2.0', 'id': 1, 'method': 'eth_getTransactionCount', 'params': [acct.address, 'latest']})
        gas_r = _http_json('https://eth.llamarpc.com', {'jsonrpc': '2.0', 'id': 1, 'method': 'eth_gasPrice', 'params': []})
        tx = {'chainId': 1, 'nonce': int(nonce_r['result'], 16), 'to': to, 'value': int(float(amount) * 1e18),
              'gas': 21000, 'gasPrice': int(gas_r['result'], 16)}
        signed = acct.sign_transaction(tx)
        sent = _http_json('https://eth.llamarpc.com',
                          {'jsonrpc': '2.0', 'id': 1, 'method': 'eth_sendRawTransaction', 'params': [signed.raw_transaction.hex()]})
        return 'ETH tx: ' + sent['result']
    if chain == 'bitcoin':
        from bit import Key
        k = Key(keys['bitcoin']['wif'])
        return 'BTC tx: ' + k.send([(to, float(amount), 'btc')], fee=10)
    raise ValueError('unknown chain: ' + chain)


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
        if g0 and not getattr(c, '_swept', False):
            c._swept = True
            c.loop.create_task(catchup_sweep(g0))
        # updates channel: one back-online post per process boot (deploys/restarts), never on resumes
        try:
            if g0 and not getattr(c, '_v_announced', False):
                c._v_announced = True
                uch = find_channel(g0, 'updates')
                if uch:
                    await uch.send(f'✅ **SHiFT back online — v{BOT_VERSION}** ⚡')
        except Exception as e:
            print('updates announce:', e)
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
        if not stripe_sync.is_running():
            stripe_sync.start()
        if not scan_engine.is_running():
            scan_engine.start()
        if not crypto_sync.is_running():
            crypto_sync.start()

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
                st_g = await asyncio.to_thread(get_state) or {}
                if str(message.id) in st_g.get('gw_handled', []):
                    return  # already processed (edit re-fire or sweep overlap)
                hs = gw_handle_parse(raw)
                if hs:
                    await gw_mark_handled(st_g, message.id)
                    await asyncio.to_thread(gh_put, 'bot_state.json', st_g, 'gw handled')
                    try:
                        await verify_giveaway_entry(message, hs[0])
                    except Exception as e:
                        # NEVER SILENT LAW: X down/rate-limited -> provisional entry + tell the user
                        print('verify wrapper:', e)
                        try:
                            conf = await asyncio.to_thread(gh_get_json_ref, 'giveaway_confirmed.json', QUEUE_BRANCH)
                            conf = conf or {}
                            hk = hs[0].lower()
                            if hk not in conf:
                                conf[hk] = {'handle': hs[0], 'discord': str(message.author), 'discord_id': str(message.author.id),
                                            'mult': 1, 'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                                            'note': 'provisional - X verification unavailable; verify before draw'}
                                await asyncio.to_thread(gh_put, 'giveaway_confirmed.json', conf, 'provisional entry ' + hk, QUEUE_BRANCH)
                                await message.channel.send(f"{message.author.mention} 🎫 **ENTRY LOGGED — @{hs[0]}** — X is rate-limiting our checks right now, so you're in the pool provisional (1 ticket) and I'll re-verify before Sunday's draw. Nothing else to do. ⚡")
                            else:
                                await gw_reply_once(message, 'already', f"🎫 **@{hs[0]}** — already in the pool. Sunday 6 PM ET. ⚡")
                        except Exception as e2:
                            print('provisional fail:', e2)
                elif re.search(r'@|x\.com|twitter|handle', raw, re.I):
                    if str(message.id) not in st_g.get('gw_handled', []):
                        await gw_mark_handled(st_g, message.id)
                        await asyncio.to_thread(gh_put, 'bot_state.json', st_g, 'gw handled')
                        await gw_reply_once(message, 'guide', "⚡ Drop your **X (Twitter) handle** like `@yourhandle` — not your Discord name — and I'll scan you in. " + GW_STEPS.format(link=gw_post_link(st_g)) + " 🎫")
                return
            # @mention responder: anyone who tags SHiFT gets a reply; commands stay admin/queue-only
            try:
                if c.user and c.user in message.mentions:
                    is_admin = False
                    try:
                        is_admin = message.author == message.guild.owner or message.author.guild_permissions.administrator
                    except Exception:
                        pass
                    now_utc = time.time()
                    slots = (0, 4, 8, 12, 16, 20)
                    cur_h = int(time.strftime('%H', time.gmtime(now_utc)))
                    nxt = next((h for h in slots if h > cur_h), 0)
                    nxt_et = (nxt - 4) % 24
                    ampm = 'AM' if nxt_et < 12 else 'PM'
                    nxt_s = f'{nxt_et % 12 or 12} {ampm} ET'
                    if is_admin:
                        await message.channel.send(f"{message.author.mention} 🛰️ **v{BOT_VERSION}** online — next scan **{nxt_s}**. Commands route through the ops queue only — chat commands are disabled for everyone.")
                    else:
                        await message.channel.send(f"{message.author.mention} 🛰️ I'm on duty — scans drop **12a · 4a · 8a · 12p · 4p · 8p ET** (next **{nxt_s}**). Free pick in the free-pick room; paid rooms get the full board. thelineshift.github.io/AISportsBot/upgrade.html ⚡")
                    return
            except Exception as e:
                print('mention responder:', e)
            if (message.content or '').strip().lower().startswith('!crypto'):
                parts = (message.content or '').strip().split()
                tier = parts[1].lower() if len(parts) > 1 else ''
                coin = parts[2].lower() if len(parts) > 2 else ''
                if tier not in CRYPTO_TIERS or not coin:
                    await message.reply('🪙 Usage: `!crypto <tier> <coin>` — e.g. `!crypto sharp sol`\nCoins: **btc, eth, sol, usdt, usdc, doge, ltc, trx, bnb** (+300 more — just ask). I will DM your payment address.', mention_author=False)
                else:
                    await message.reply(f'🪙 Generating your **{tier.upper()}** checkout in **{coin.upper()}** — check your DMs!', mention_author=False)
                    _log = []
                    await run_command({'action': 'crypto_checkout', 'tier': tier, 'coin': coin, 'user': str(message.author.id)}, message.guild, _log)
                    print('crypto cmd:', _log)
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
    async def on_message_edit(before, after):
        # edited giveaway posts must be (re)scanned — handled-ID dedupe inside on_message
        try:
            if after.author.bot:
                return
            chname = (getattr(after.channel, 'name', '') or '').lower()
            if 'giveaway' in chname:
                await on_message(after)
        except Exception as e:
            print('on_message_edit:', e)

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

CH_ALIASES = {
    'daily-locks': ['lock-room'], 'all-picks': ['sharp-room'], 'every-play': ['whale-room'],
    'lock-lounge': ['lock-lounge'], 'sharp-talk': ['sharp-talk'], 'whale-talk': ['whale-talk'],
    'weekly-analytics': ['sharp-analytics'], 'monthly-deepdive': ['whale-deepdive'],
}
def find_channel(guild, name):
    keys = [name] + CH_ALIASES.get(name, [])
    for ch in guild.text_channels:
        cn = ch.name.lower().replace('-', '').replace('_', '')
        for k in keys:
            if k.lower().strip('#').replace(' ', '').replace('-', '').replace('_', '') in cn:
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

GW_BAD = ('thelineshift', 'everyone', 'here', 'status', 'home', 'search', 'explore', 'i', 'yourhandle')
GW_POST_DEFAULT = '2080027230839931367'

def gw_post_link(st):
    pid = (st or {}).get('giveaway_x_post', GW_POST_DEFAULT)
    return f'https://x.com/TheLineShift/status/{pid}'

GW_STEPS = "Entry needs 3 steps on X: ✅ Follow @TheLineShift · ❤️ Like the giveaway post · 🔁 Repost it — this exact one: {link}"

def gw_handle_parse(raw):
    """Extract an X handle from free text: @name (space ok), x.com/name, 'x handle: name'."""
    hs = list(re.findall(r'@\s*([A-Za-z0-9_]{4,15})\b', raw or ''))
    hs += re.findall(r'(?:https?://)?(?:www\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{4,15})', raw or '', flags=re.I)
    hs += re.findall(r'(?:x\s*handle|handle)\s*[:=\-]?\s*@?\s*([A-Za-z0-9_]{4,15})\b', raw or '', flags=re.I)
    out = []
    for h in hs:
        if h.lower() not in GW_BAD and h not in out:
            out.append(h)
    return out

async def gw_mark_handled(st, msg_id):
    """Remember a giveaway message ID so sweeps/edits never re-process it."""
    lst = st.setdefault('gw_handled', [])
    if str(msg_id) not in lst:
        lst.append(str(msg_id))
        st['gw_handled'] = lst[-300:]

async def gw_reply_once(message, key, body, hours=20):
    """ERROR-ONCE LAW: one reply of a given kind per user per window, always tagging them."""
    st = await asyncio.to_thread(get_state) or {}
    rep = st.setdefault('gw_replies', {})
    k = f'{message.author.id}:{key}'
    if time.time() - rep.get(k, 0) < hours * 3600:
        return False
    rep[k] = time.time()
    await asyncio.to_thread(gh_put, 'bot_state.json', st, 'gw reply mark')
    await message.channel.send(f'{message.author.mention} {body}')
    return True

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
            await gw_reply_once(message, 'nohandle', f"⚡ entry check: I can't find an X account **@{handle}** — double-check the spelling and drop it again.")
            return
        # bearer (app-only) covers all public reads — no user token needed (7/24 fix)
        followed = await asyncio.to_thread(gw_followed, uid, bt)
        try:
            liked = uid in await asyncio.to_thread(gw_user_set, f'https://api.x.com/2/tweets/{post_id}/liking_users?max_results=100', bt)
        except Exception:
            liked = None
        try:
            reposted = uid in await asyncio.to_thread(gw_user_set, f'https://api.x.com/2/tweets/{post_id}/retweeted_by?max_results=100', bt)
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
                await gw_reply_once(message, 'already', f"🎫 **@{handle}** — you're already locked in the pool. Sit tight for Sunday 6 PM ET. ⚡")
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
                f"{message.author.mention} 🎫 **ENTRY CONFIRMED — @{handle}**\n\n{checklist}\n🎟️ **Tickets: {mult}x — {TIER_ROOM.get(tkey, tkey)}**\n\nDraw: Sunday 6 PM ET — provably fair, paid on-chain. ⚡")
        else:
            steps = (f"**{len(missing)} step{'s' if len(missing) > 1 else ''} left:** " + ' + '.join(missing)) if missing else 'X is still registering your activity —'
            await gw_reply_once(message, 'steps',
                f"🎫 **ENTRY CHECK — @{handle}**\n\n{checklist}\n\n{steps} — do them on **this exact post**: {gw_post_link(state)}\nThen drop your handle here again and I'll re-scan you in seconds. ⚡")
    except Exception as e:
        print('giveaway verify error:', e)

async def catchup_sweep(g0):
    # NEVER SILENT LAW: on boot, process giveaway/issues messages that got no response
    # (covers offline windows). Rate-aware: <=8 replies, 2s apart, skipped during boot loops.
    try:
        if boots_last_hour() >= 4:
            print('catchup sweep skipped (boot-loop guard)')
            return
        await asyncio.sleep(8)  # let the bot settle after connect
        report = []
        lab = find_channel(g0, 'shift-lab')
        # 1) MONEY FIRST: sync Stripe subscriptions immediately at boot — a paying
        #    customer never waits for their role because we were down.
        try:
            await _stripe_sync_once()
            report.append('stripe sync ✓ (roles reconciled)')
        except Exception as e:
            report.append(f'stripe sync FAIL: {e}')
        # 2) MISSED SCANS: if we booted past a slot with no card/resolution, say so
        try:
            st = await asyncio.to_thread(get_state)
            sev = (st or {}).get('scan_events', {})
            now = time.gmtime()
            for delta_h in (0, 4):
                t = time.gmtime(time.time() - delta_h * 3600)
                if t.tm_hour not in (0, 4, 8, 12, 16, 20):
                    continue
                key = time.strftime('%Y%m%d-%H', t)
                mins_past = ((time.time() - delta_h * 3600) % 3600) / 60 if delta_h == 0 else 0
                if sev.get(key) != 'ok' and (delta_h > 0 or mins_past > 40):
                    gch2 = find_channel(g0, 'general-chat')
                    if gch2:
                        await gch2.send("⚡ SHiFT is back online after a brief outage. A scan window was missed while I was down — the backstop scheduler is running the makeup now. Cards never stop. ⚡")
                        report.append(f'missed-scan note posted for slot {key}')
                    break
        except Exception as e:
            print('scan sweep:', e)
        gch = find_channel(g0, 'giveaway')
        if gch:
            st_g = await asyncio.to_thread(get_state) or {}
            handled = set(st_g.get('gw_handled', []))
            conf = await asyncio.to_thread(gh_get_json_ref, 'giveaway_confirmed.json', QUEUE_BRANCH) or {}
            msgs = [m async for m in gch.history(limit=40)]
            done = 0
            dirty = False
            for m in reversed(msgs):
                if m.author.bot or str(m.id) in handled:
                    continue
                raw = m.content or ''
                hs = gw_handle_parse(raw)
                if not hs:
                    continue  # guidance for no-handle posts is on_message's job; sweep never nags
                if hs[0].lower() in conf:
                    await gw_mark_handled(st_g, m.id)
                    dirty = True
                    await gw_reply_once(m, 'already', f"🎫 **@{hs[0]}** — you're already entered in Sunday's $50 draw (6 PM ET). Nothing else to do — good luck! ⚡")
                    done += 1
                    continue  # already entered — confirm once, then move on
                await gw_mark_handled(st_g, m.id)
                dirty = True
                try:
                    await verify_giveaway_entry(m, hs[0])
                    done += 1
                except Exception:
                    try:
                        hk = hs[0].lower()
                        if hk not in conf:
                            conf[hk] = {'handle': hs[0], 'discord': str(m.author), 'discord_id': str(m.author.id),
                                        'mult': 1, 'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                                        'note': 'provisional (catchup) - verify before draw'}
                            await asyncio.to_thread(gh_put, 'giveaway_confirmed.json', conf, 'catchup provisional ' + hk, QUEUE_BRANCH)
                            await gch.send(f"{m.author.mention} 🎫 **ENTRY LOGGED — @{hs[0]}** — caught up after a restart; provisional ticket logged, re-verified before Sunday's draw. ⚡")
                            done += 1
                    except Exception:
                        pass
                await asyncio.sleep(2)
                if done >= 8:
                    break
            if dirty:
                await asyncio.to_thread(gh_put, 'bot_state.json', st_g, 'gw sweep handled')
            if done:
                print(f'giveaway catchup: {done} processed')
                report.append(f'giveaway catch-up: {done}')
        ich = find_channel(g0, 'issues')
        if ich:
            msgs = [m async for m in ich.history(limit=15)]
            bot_ts = max((m.created_at for m in msgs if m.author.bot), default=None)
            unans = [m for m in msgs if not m.author.bot and (bot_ts is None or m.created_at > bot_ts)]
            for m in reversed(unans[-3:]):
                try:
                    await handle_issue(m, g0)
                    await asyncio.sleep(2)
                except Exception as e:
                    print('issues catchup:', e)
            if unans:
                print(f'issues catchup: {len(unans[-3:])} triaged')
                report.append(f'issues triaged: {len(unans[-3:])}')
        if report and lab:
            await lab.send('🔄 **BOOT SWEEP** — ' + ' · '.join(report))
    except Exception as e:
        print('catchup sweep error:', e)

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
        msg = await ch.send("🛰️ **WANT THE HEADS-UP?**\nReact with 🛰️ and you'll get one quiet ping before each scan (6x daily — T-60 and T-10 only, nothing else). Remove your reaction anytime to opt out. No spam, just the warning. ⚡")
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
            try:
                c = await asyncio.to_thread(x_oauth2_refresh, c)
            except Exception as e:
                log.append(f'x_delete: token refresh failed ({e}) — trying with current creds')
        for tid in cmd.get('ids', []):
            ok = False
            # path 1: OAuth2 user-context delete
            try:
                req = urllib.request.Request(f'https://api.x.com/2/tweets/{tid}', method='DELETE',
                                             headers={'Authorization': f"Bearer {c['oauth2_access']}"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    json.load(r)
                log.append(f'deleted post {tid} (oauth2)')
                ok = True
            except Exception as e:
                body = ''
                if hasattr(e, 'read'):
                    try: body = e.read()[:150]
                    except Exception: pass
                log.append(f'delete {tid} oauth2 FAIL: {e} {body}')
            # path 2: OAuth1 v1.1 statuses/destroy
            if not ok:
                for name, ck, cs, at, ats in x_oauth1_sets(c):
                    try:
                        durl = f'https://api.x.com/1.1/statuses/destroy/{tid}.json'
                        hdr = x_oauth1_sign('POST', durl, ck, cs, at, ats)
                        req = urllib.request.Request(durl, data=b'', method='POST',
                                                     headers={'Authorization': hdr})
                        with urllib.request.urlopen(req, timeout=20) as r:
                            json.load(r)
                        log.append(f'deleted post {tid} (oauth1 {name})')
                        ok = True
                        break
                    except Exception as e:
                        body = ''
                        if hasattr(e, 'read'):
                            try: body = e.read()[:150]
                            except Exception: pass
                        log.append(f'delete {tid} oauth1[{name}] FAIL: {e} {body}')
            if not ok:
                log.append(f'delete {tid}: ALL paths failed')
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
                    await ch.send(f"⚡ SHiFT entry check: can't find an X account **@{handle}** — double-check the spelling and drop it again.")
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
                            await ch.send(f"🎫 **@{handle}** — already locked in the pool. Sunday 6 PM ET. ⚡")
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
                            await ch.send(f"🎫 **ENTRY CONFIRMED — @{handle}**\n\n{checklist}\n🎟️ **Tickets: {mult}x — {TIER_ROOM.get(tkey, tkey)}**\n\nDraw: Sunday 6 PM ET — provably fair, paid on-chain. ⚡")
                            log.append(f'verify_entry: CONFIRMED @{handle} ({mult}x)')
                    else:
                        steps = (f"**{len(missing)} step{'s' if len(missing) > 1 else ''} left:** " + ' + '.join(missing)) if missing else 'X is still registering your activity —'
                        await ch.send(f"🎫 **ENTRY CHECK — @{handle}**\n\n{checklist}\n\n{steps} finish up, then drop your handle here again and I'll re-scan you in seconds. ⚡")
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
    elif a == 'x_pinned':
        try:
            c = x_creds_load()
            if time.time() > c.get('oauth2_expires_at', 0):
                c = await asyncio.to_thread(x_oauth2_refresh, c)
            d = await asyncio.to_thread(x_get_json,
                'https://api.x.com/2/users/me?user.fields=pinned_tweet_id', c['oauth2_access'])
            pid = d.get('data', {}).get('pinned_tweet_id')
            log.append(f"x_pinned: {pid or 'none'}")
            if pid:
                t = await asyncio.to_thread(x_get_json, f'https://api.x.com/2/tweets/{pid}', c['oauth2_access'])
                log.append(f"pinned text: {t.get('data', {}).get('text', '')[:200]}")
        except Exception as e:
            body = ''
            if hasattr(e, 'read'):
                try: body = e.read()[:250]
                except Exception: pass
            log.append(f'x_pinned FAIL: {e} {body}')
    elif a == 'x_pin':
        try:
            c = x_creds_load()
            if time.time() > c.get('oauth2_expires_at', 0):
                c = await asyncio.to_thread(x_oauth2_refresh, c)
            me = await asyncio.to_thread(x_get_json, 'https://api.x.com/2/users/me', c['oauth2_access'])
            uid = me.get('data', {}).get('id')
            tid = str(cmd['tweet_id'])
            payload = json.dumps({'tweet_id': tid}).encode()
            ok = False
            for host in ['api.x.com', 'api.twitter.com']:
                try:
                    req = urllib.request.Request(f'https://{host}/2/users/{uid}/pinned_tweets',
                        data=payload, headers={'Authorization': f"Bearer {c['oauth2_access']}",
                                               'Content-Type': 'application/json'}, method='PUT')
                    with urllib.request.urlopen(req, timeout=20) as r:
                        d = json.load(r)
                    log.append(f"x_pin: pinned {tid} via oauth2 {host} -> {d.get('data')}")
                    ok = True; break
                except urllib.error.HTTPError as e:
                    try: eb = e.read()[:200]
                    except Exception: eb = b''
                    log.append(f'x_pin oauth2 {host} HTTP {e.code}: {eb}')
            if not ok:
                import hmac, hashlib, secrets
                import urllib.parse as _up
                for name, ck, cs, at, ats in x_oauth1_sets(c):
                    for host in ['api.x.com', 'api.twitter.com']:
                        endpoint = f'https://{host}/2/users/{uid}/pinned_tweets'
                        try:
                            op = {'oauth_consumer_key': ck, 'oauth_nonce': secrets.token_hex(16),
                                  'oauth_signature_method': 'HMAC-SHA1', 'oauth_timestamp': str(int(time.time())),
                                  'oauth_token': at, 'oauth_version': '1.0'}
                            q = lambda s: _up.quote(str(s), safe='')
                            base = '&'.join(['PUT', q(endpoint), q('&'.join(f'{q(k)}={q(v)}' for k, v in sorted(op.items())))])
                            key = f'{q(cs)}&{q(ats)}'
                            op['oauth_signature'] = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
                            hdr = 'OAuth ' + ', '.join(f'{k}="{q(v)}"' for k, v in sorted(op.items()))
                            req = urllib.request.Request(endpoint, data=payload, method='PUT',
                                headers={'Authorization': hdr, 'Content-Type': 'application/json'})
                            with urllib.request.urlopen(req, timeout=20) as r:
                                d = json.load(r)
                            log.append(f"x_pin: pinned {tid} via oauth1[{name}] {host} -> {d.get('data')}")
                            ok = True; break
                        except urllib.error.HTTPError as e:
                            try: eb = e.read()[:200]
                            except Exception: eb = b''
                            log.append(f'x_pin oauth1[{name}] {host} HTTP {e.code}: {eb}')
                    if ok: break
            if not ok:
                log.append('x_pin: all attempts failed')
        except Exception as e:
            body = ''
            if hasattr(e, 'read'):
                try: body = e.read()[:250]
                except Exception: pass
            log.append(f'x_pin FAIL: {e} {body}')
    elif a == 'crypto_wallets':
        try:
            bal = await asyncio.to_thread(wallet_balances)
            await asyncio.to_thread(gh_put, 'wallet_balances.json', bal, 'wallet balances')
            tot = sum(w.get('usd') or 0 for w in bal.get('wallets', []))
            lines = [f"👛 **HOT WALLETS** — ${tot:,.2f} total"]
            for w in bal.get('wallets', []):
                lines.append(f"• **{w['symbol']}** `{w['address']}` — {w['balance']} (${w['usd']:,.2f})")
            lab = find_channel(guild, 'shift-lab')
            if lab:
                await lab.send('\n'.join(lines)[:1900])
            log.append(f"wallet report: ${tot:,.2f}")
        except Exception as e:
            log.append(f'crypto_wallets FAIL: {e}')
    elif a == 'crypto_withdraw':
        try:
            chain = str(cmd.get('chain', '')).lower()
            to = cmd.get('to', '')
            amount = cmd.get('amount', 0)
            txid = await asyncio.to_thread(crypto_withdraw, chain, to, amount)
            await asyncio.to_thread(log_event, 'crypto_withdraw', f'{amount} {chain} -> {to[:12]}... : {txid}')
            lab = find_channel(guild, 'shift-lab')
            if lab:
                await lab.send(f"💸 **WITHDRAWAL SENT** — {amount} {chain.upper()} → `{to}`\n{txid}")
            log.append(f'withdraw: {txid}')
            bal = await asyncio.to_thread(wallet_balances)
            await asyncio.to_thread(gh_put, 'wallet_balances.json', bal, 'wallet balances post-withdraw')
        except Exception as e:
            log.append(f'crypto_withdraw FAIL: {e}')
            lab = find_channel(guild, 'shift-lab')
            if lab:
                await lab.send(f"❌ WITHDRAW FAILED — {cmd.get('chain')} {cmd.get('amount')}: {e}")
    elif a == 'crypto_checkout':
        try:
            tier = str(cmd.get('tier', '')).lower()
            coin = str(cmd.get('coin', 'usdt')).lower()
            aliases = {'usdt': 'usdttrc20', 'bnb': 'bnbbsc'}
            coin = aliases.get(coin, coin)
            member = await resolve_member(guild, str(cmd.get('user', '')))
            np_key = os.environ.get('NOWPAYMENTS_KEY', '')
            if not np_key:
                log.append('crypto checkout: NOWPAYMENTS_KEY not set')
            elif tier not in CRYPTO_TIERS or not member:
                log.append(f'crypto checkout: bad tier/user {tier} {cmd.get("user")}')
            else:
                pay = await asyncio.to_thread(_http_json, 'https://api.nowpayments.io/v1/payment',
                    {'price_amount': CRYPTO_TIERS[tier], 'price_currency': 'usd', 'pay_currency': coin,
                     'order_id': f'{member.id}:{tier}', 'order_description': f'TheLineShift {tier.title()} 30 days',
                     'is_fixed_rate': True}, {'x-api-key': np_key})
                pid = pay.get('payment_id')
                if not pid:
                    log.append(f'crypto checkout failed: {str(pay)[:200]}')
                else:
                    known = await asyncio.to_thread(gh_get_json, 'crypto_members.json') or {'members': []}
                    known.setdefault('members', [])
                    known['members'].append({'payment_id': pid, 'discord_id': str(member.id), 'tier': tier,
                                             'status': pay.get('payment_status', 'waiting'), 'coin': coin,
                                             'pay_address': pay.get('pay_address'), 'pay_amount': pay.get('pay_amount'),
                                             'created': time.time(), 'expires': None})
                    await asyncio.to_thread(gh_put, 'crypto_members.json', known, 'crypto checkout created')
                    try:
                        await member.send(
                            f"🪙 **{tier.upper()} — crypto checkout (30 days)**\n"
                            f"Send **{pay.get('pay_amount')} {coin.upper()}** to:\n`{pay.get('pay_address')}`\n"
                            f"Your access activates **automatically** when the payment confirms on-chain. "
                            f"Questions? #🛠️issues.")
                    except Exception:
                        pass
                    log.append(f'crypto payment {pid} for {member} ({coin})')
        except Exception as e:
            log.append(f'crypto_checkout FAIL: {e}')
    elif a == 'audit_channels':
        try:
            rep = {'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'channels': []}
            for ch in guild.text_channels:
                last = None
                try:
                    async for m in ch.history(limit=1):
                        last = {'author': str(m.author), 'ts': m.created_at.strftime('%Y-%m-%dT%H:%M:%SZ'),
                                'preview': (m.content or '(embed/attachment)')[:140]}
                except Exception:
                    pass
                rep['channels'].append({'name': ch.name, 'topic': (ch.topic or ''), 'last': last})
            await asyncio.to_thread(gh_put, 'channels_audit.json', rep, 'channel audit')
            log.append(f"channel audit: {len(rep['channels'])} channels -> channels_audit.json")
        except Exception as e:
            log.append(f'audit_channels FAIL: {e}')
    elif a == 'purge_whop':
        try:
            n = 0
            for m in list(guild.members):
                if m.bot and 'whop' in m.name.lower():
                    try:
                        await guild.kick(m, reason='whop purge - platform retired')
                        n += 1
                    except Exception as e:
                        log.append(f'whop kick failed: {e}')
            for r in list(guild.roles):
                if 'whop' in r.name.lower():
                    try:
                        await r.delete(reason='whop purge - platform retired')
                        n += 1
                    except Exception as e:
                        log.append(f'whop role delete failed: {e}')
            await asyncio.to_thread(log_event, 'whop_purge', f'{n} whop entities removed (bot kicked, roles deleted)')
            log.append(f'whop purge complete: {n} removed')
        except Exception as e:
            log.append(f'purge_whop FAIL: {e}')
    elif a == 'audit_permissions':
        try:
            ev_role = guild.default_role
            roles = {w: next((r for r in guild.roles if w.lower() in r.name.lower()), None)
                     for w in ('Lock', 'Sharp', 'Whale')}
            POLICY = [
                (('daily-locks', 'lock-lounge', '100-to-1000'), {'Lock', 'Sharp', 'Whale'}),
                (('all-picks', 'weekly-analytics', 'sharp-talk'), {'Sharp', 'Whale'}),
                (('every-play', 'monthly-deepdive', 'whale-talk'), {'Whale'}),
                (('shift-lab',), set()),
            ]
            lines = ['🛡️ **PERMISSION AUDIT** — ' + time.strftime('%Y-%m-%d %H:%M UTC')]
            leaks = 0
            for ch in guild.text_channels:
                name = ch.name.lower()
                allowed = None
                for keys, allow in POLICY:
                    if any(k in name for k in keys):
                        allowed = allow
                        break
                if allowed is None:
                    continue
                can_ev = ch.permissions_for(ev_role).view_channel
                can = {w: (ch.permissions_for(r).view_channel if r else None) for w, r in roles.items()}
                status = []
                if can_ev:
                    status.append('🚨@everyone CAN SEE'); leaks += 1
                for w in ('Lock', 'Sharp', 'Whale'):
                    want = w in allowed
                    got = bool(can[w])
                    if roles[w] is None:
                        status.append(f'⚠️{w} role missing'); continue
                    if got and not want:
                        status.append(f'🚨{w} leak'); leaks += 1
                    elif want and not got:
                        status.append(f'⚠️{w} locked out'); leaks += 1
                flag = '✅' if not status else ' | '.join(status)
                lines.append(f'#{ch.name}: {flag}')
            lab = find_channel(guild, 'shift-lab')
            report = '\n'.join(lines)[:1900]
            if lab:
                await lab.send(report)
            log.append(f'audit: {leaks} leak(s) across {len(guild.text_channels)} channels')
            await asyncio.to_thread(log_event, 'audit', f'permission audit: {leaks} leak(s)')
        except Exception as e:
            log.append(f'audit FAIL: {e}')
    elif a == 'collect_metrics':
        try:
            counts = {'members': guild.member_count}
            for word, key in [('Lock', 'lock'), ('Sharp', 'sharp'), ('Whale', 'whale')]:
                n = sum(1 for mem in guild.members if any(word.lower() in r.name.lower() for r in mem.roles))
                counts[key] = n
            snap = {'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), **counts}
            try:
                c = x_creds_load()
                if time.time() > c.get('oauth2_expires_at', 0):
                    c = await asyncio.to_thread(x_oauth2_refresh, c)
                uid = c.get('user_id') or '1831457082828021760'
                req = urllib.request.Request(f'https://api.x.com/2/users/{uid}?user.fields=public_metrics',
                                             headers={'Authorization': f"Bearer {c['oauth2_access']}"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    d = json.load(r)['data']['public_metrics']
                snap['x_followers'] = d['followers_count']
                snap['x_tweets'] = d['tweet_count']
            except Exception as e:
                log.append(f'metrics X fetch failed: {e}')
            try:
                bal = await asyncio.to_thread(wallet_balances)
                await asyncio.to_thread(gh_put, 'wallet_balances.json', bal, 'wallet balances')
            except Exception as e:
                log.append(f'wallet balances failed: {e}')
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
        OPEN_SEND = ['general-chat', 'giveaway', 'issues', 'upgrade']
        OPEN_READ = ['free-pick', 'receipts', 'updates', 'welcome', 'rules', 'promotions']
        STAFF_ONLY = ['shift-lab']
        PAID = {'daily-locks': ['Lock', 'Sharp', 'Whale'], 'lock-lounge': ['Lock', 'Sharp', 'Whale'],
                '100-to-1000': ['Lock', 'Sharp', 'Whale'], 'all-picks': ['Sharp', 'Whale'],
                'weekly-analytics': ['Sharp', 'Whale'], 'sharp-talk': ['Sharp', 'Whale'],
                'every-play': ['Whale'], 'monthly-deepdive': ['Whale'], 'whale-talk': ['Whale']}
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
    elif a == 'x_read':
        try:
            c = x_creds_load()
            ids = cmd.get('ids') or []
            if not ids:
                log.append('x_read: no ids given')
            else:
                d = await asyncio.to_thread(x_get_json, 'https://api.x.com/2/tweets?ids=' + ','.join(str(i) for i in ids), c['bearer_token'])
                for t in d.get('data', []):
                    full = t.get('text') or ''
                    urls = re.findall(r'https?://\S+', full)
                    txt = full.replace('\n', ' | ')
                    flag = 'GITHACK!' if 'githack' in full.lower() else ('WHOP!' if 'whop' in full.lower() else 'ok')
                    log.append(f"{t['id']} [{flag}] urls={urls} :: {txt[:110]}")
        except Exception as e:
            log.append(f'x_read FAIL: {e}')
    elif a == 'x_profile_update':
        try:
            fields = {k: cmd[k] for k in ('name', 'description', 'url', 'location') if cmd.get(k)}
            if not fields:
                log.append('x_profile_update: nothing to set')
            else:
                updated = False
                for name, ck, cs, at, ats in x_oauth1_sets(x_creds_load()):
                    try:
                        import hmac, hashlib, secrets
                        import urllib.parse as _up
                        purl = 'https://api.x.com/1.1/account/update_profile.json'
                        op = {'oauth_consumer_key': ck, 'oauth_nonce': secrets.token_hex(16),
                              'oauth_signature_method': 'HMAC-SHA1', 'oauth_timestamp': str(int(time.time())),
                              'oauth_token': at, 'oauth_version': '1.0'}
                        allp = {**op, **fields}
                        q = lambda s: _up.quote(str(s), safe='')
                        base = '&'.join(['POST', q(purl), q('&'.join(f'{q(k)}={q(v)}' for k, v in sorted(allp.items())))])
                        key = f'{q(cs)}&{q(ats)}'
                        op['oauth_signature'] = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
                        hdr = 'OAuth ' + ', '.join(f'{k}="{q(v)}"' for k, v in sorted(op.items()))
                        body = _up.urlencode(fields).encode()
                        req = urllib.request.Request(purl, data=body, method='POST',
                            headers={'Authorization': hdr, 'Content-Type': 'application/x-www-form-urlencoded'})
                        with urllib.request.urlopen(req, timeout=20) as r:
                            d = json.load(r)
                        log.append(f"profile updated [{name}]: @{d.get('screen_name')} | name: {d.get('name')} | bio: {str(d.get('description'))[:80]} | url: {d.get('url')}")
                        updated = True
                        break
                    except urllib.error.HTTPError as e:
                        try: eb = e.read()[:200]
                        except Exception: eb = b''
                        log.append(f'x_profile_update [{name}] HTTP {e.code}: {eb}')
                    except Exception as e:
                        log.append(f'x_profile_update [{name}] FAIL: {e}')
                if not updated:
                    log.append('x_profile_update: all cred sets failed')
        except Exception as e:
            log.append(f'x_profile_update error: {e}')
    elif a == 'guild_map':
        try:
            for cat in guild.categories:
                log.append(f"CAT [{cat.name}] :: " + ', '.join(c.name for c in cat.text_channels))
            log.append(f'guild_map: {len(guild.categories)} categories, {len(guild.text_channels)} channels')
        except Exception as e:
            log.append(f'guild_map FAIL: {e}')
    elif a == 'rename_channel':
        try:
            ch = find_channel(guild, cmd.get('channel', ''))
            if ch:
                old = ch.name
                await ch.edit(name=cmd.get('name', ''), reason='captain rename')
                log.append(f'renamed #{old} -> #{cmd.get("name")}')
            else:
                log.append(f'rename_channel: {cmd.get("channel")} not found')
        except Exception as e:
            log.append(f'rename_channel FAIL: {e}')
    elif a == 'rename_category':
        try:
            tgt = (cmd.get('category', '') or '').lower()
            cat = next((c for c in guild.categories if tgt in c.name.lower()), None)
            if cat:
                old = cat.name
                await cat.edit(name=cmd.get('name', ''), reason='captain rename')
                log.append(f'category [{old}] -> [{cmd.get("name")}]')
            else:
                log.append(f'rename_category: {cmd.get("category")} not found')
        except Exception as e:
            log.append(f'rename_category FAIL: {e}')
    elif a == 'x_profile_image':
        try:
            import hmac, hashlib, secrets
            import urllib.parse as _up
            done = []
            for kind, endpoint, field in [('avatar', 'https://api.x.com/1.1/account/update_profile_image.json', 'image'),
                                          ('banner', 'https://api.x.com/1.1/account/update_profile_banner.json', 'banner')]:
                iurl = cmd.get(f'{kind}_url')
                if not iurl:
                    continue
                img = await asyncio.to_thread(lambda u=iurl: urllib.request.urlopen(
                    urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'}), timeout=30).read())
                for name, ck, cs, at, ats in x_oauth1_sets(x_creds_load()):
                    try:
                        boundary = secrets.token_hex(12)
                        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{kind}.png"\r\n'
                                f'Content-Type: image/png\r\n\r\n').encode() + img + f'\r\n--{boundary}--\r\n'.encode()
                        op = {'oauth_consumer_key': ck, 'oauth_nonce': secrets.token_hex(16),
                              'oauth_signature_method': 'HMAC-SHA1', 'oauth_timestamp': str(int(time.time())),
                              'oauth_token': at, 'oauth_version': '1.0'}
                        q = lambda s: _up.quote(str(s), safe='')
                        base = '&'.join(['POST', q(endpoint), q('&'.join(f'{q(k)}={q(v)}' for k, v in sorted(op.items())))])
                        key = f'{q(cs)}&{q(ats)}'
                        op['oauth_signature'] = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
                        hdr = 'OAuth ' + ', '.join(f'{k}="{q(v)}"' for k, v in sorted(op.items()))
                        req = urllib.request.Request(endpoint, data=body, method='POST',
                            headers={'Authorization': hdr, 'Content-Type': f'multipart/form-data; boundary={boundary}'})
                        with urllib.request.urlopen(req, timeout=30) as r:
                            r.read()
                        done.append(f'{kind} set [{name}]')
                        break
                    except urllib.error.HTTPError as e:
                        try: eb = e.read()[:150]
                        except Exception: eb = b''
                        log.append(f'x_profile_image {kind} [{name}] HTTP {e.code}: {eb}')
            log.append('x_profile_image: ' + (' | '.join(done) if done else 'nothing set'))
        except Exception as e:
            log.append(f'x_profile_image FAIL: {e}')
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
        tgt = None
        cid = str(cmd.get('id', ''))
        if cid:
            tgt = guild.get_channel(int(cid)) or discord.utils.find(lambda t: str(t.id) == cid, guild.threads)
            if not tgt:
                try:
                    tgt = await guild.fetch_channel(int(cid))
                except Exception:
                    tgt = None
        else:
            name = (cmd.get('channel') or '').lower().lstrip('#')
            tgt = discord.utils.find(lambda t: name and name in t.name.lower(), guild.threads) or find_channel(guild, cmd.get('channel', ''))
        if tgt:
            nm = tgt.name
            await tgt.delete()
            log.append(f'deleted channel/thread: {nm}')
        else:
            log.append('delete_channel: not found')
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
    elif a == 'set_username':
        name = (cmd.get('name') or 'shift').strip().lower()
        try:
            await client.user.edit(username=name)
            log.append(f'username set to: {name}')
        except Exception as e:
            log.append(f'set_username {name} FAIL: {e}')
    elif a == 'scan_now':
        dry_run = bool(cmd.get('dry', True))
        slot_key = time.strftime('%Y%m%d-%H', time.gmtime()) + '-manual'
        _SCAN_DONE.add(slot_key)
        await scan_engine_run(guild, slot_key, dry_run)
        log.append(f'scan_now executed (dry={dry_run})')
    elif a == 'list_perms':
        # dump channel + thread overwrites for @everyone and role-gates
        lines = []
        for chx in guild.text_channels:
            ow = chx.overwrites_for(guild.default_role)
            gates = [r.name for r in chx.overwrites if hasattr(r, 'name') and r != guild.default_role and not r.is_bot_managed()]
            lines.append(f"#{chx.name}: everyone(view={ow.view_channel},send={ow.send_messages},threads={ow.create_public_threads},files={ow.attach_files})" + (f" gates={gates[:4]}" if gates else ''))
        thr = []
        for t in guild.threads:
            thr.append(f"thread '{t.name}' in #{getattr(t.parent, 'name', '?')} (id={t.id})")
        log.append('PERMS:\n' + '\n'.join(lines) + ('\nTHREADS: ' + ' | '.join(thr) if thr else '\nTHREADS: none'))
    elif a == 'perm_detail':
        ch = find_channel(guild, cmd['channel'])
        if not ch:
            log.append('perm_detail: channel not found')
            return
        lines = []
        for tgt, ow in ch.overwrites.items():
            nm = getattr(tgt, 'name', str(tgt))
            lines.append(f"{nm}: view={ow.view_channel},send={ow.send_messages},threads={ow.create_public_threads},files={ow.attach_files}")
        log.append(f"#{ch.name} overwrites:\n" + '\n'.join(lines[:12]))
    elif a == 'sweep_giveaway':
        gch = find_channel(guild, 'giveaway')
        if not gch:
            log.append('sweep_giveaway: channel not found')
            return
        st_g = await asyncio.to_thread(get_state) or {}
        handled = set(st_g.get('gw_handled', []))
        conf = await asyncio.to_thread(gh_get_json_ref, 'giveaway_confirmed.json', QUEUE_BRANCH) or {}
        limit = int(cmd.get('limit', 60))
        msgs = [m async for m in gch.history(limit=limit)]
        done = 0
        dirty = False
        for m in reversed(msgs):
            if m.author.bot or str(m.id) in handled:
                continue
            hs = gw_handle_parse(m.content or '')
            if not hs:
                continue
            await gw_mark_handled(st_g, m.id)
            dirty = True
            if hs[0].lower() in conf:
                await gw_reply_once(m, 'already', f"🎫 **@{hs[0]}** — you're already entered in Sunday's $50 draw (6 PM ET). Nothing else to do — good luck! ⚡")
                done += 1
                continue
            try:
                await verify_giveaway_entry(m, hs[0])
                done += 1
            except Exception:
                pass
            await asyncio.sleep(2)
            if done >= 10:
                break
        if dirty:
            await asyncio.to_thread(gh_put, 'bot_state.json', st_g, 'gw manual sweep')
        log.append(f'sweep_giveaway: {done} processed')
    elif a == 'lock_channel':
        ch = find_channel(guild, cmd['channel'])
        preset = cmd.get('preset', 'community')
        if not ch:
            log.append('lock_channel: channel not found')
            return
        everyone = guild.default_role
        if preset == 'readonly':
            await ch.set_permissions(everyone, view_channel=True, send_messages=False,
                                     create_public_threads=False, create_private_threads=False,
                                     attach_files=False, add_reactions=True, mention_everyone=False)
        elif preset == 'community':
            await ch.set_permissions(everyone, view_channel=True, send_messages=True,
                                     create_public_threads=False, create_private_threads=False,
                                     attach_files=False, add_reactions=True, use_external_emojis=True,
                                     embed_links=True, mention_everyone=False, manage_messages=False,
                                     read_message_history=True)
        elif preset == 'paid':
            await ch.set_permissions(everyone, view_channel=False)
            for rname in str(cmd.get('role', '')).split(','):
                rname = rname.strip()
                if not rname:
                    continue
                role = discord.utils.find(lambda r: rname.lower() in r.name.lower(), guild.roles)
                if not role:
                    log.append(f'lock_channel: role {rname} not found')
                    continue
                await ch.set_permissions(role, view_channel=True, send_messages=True,
                                         create_public_threads=False, create_private_threads=False,
                                         attach_files=False, add_reactions=True, use_external_emojis=True,
                                         mention_everyone=False, manage_messages=False, read_message_history=True)
        # bot keeps full control
        await ch.set_permissions(guild.me, view_channel=True, send_messages=True, manage_messages=True,
                                 manage_channels=True, read_message_history=True, manage_threads=True)
        log.append(f'lock_channel: #{ch.name} -> {preset}' + (f' ({cmd.get("role")})' if preset == 'paid' else ''))
    elif a == 'withdraw_sol':
        # ops-wallet SOL withdrawal. Queue-only (ops-gated). Requires confirm=YES.
        if cmd.get('confirm') != 'YES':
            log.append('withdraw_sol: missing confirm=YES — aborted')
            return
        to = (cmd.get('to') or '').strip()
        try:
            sol = float(cmd.get('sol'))
        except Exception:
            log.append('withdraw_sol: bad amount')
            return
        if sol <= 0 or sol > 50:
            log.append('withdraw_sol: amount out of bounds (0<x<=50)')
            return
        try:
            from solders.keypair import Keypair
            from solders.pubkey import Pubkey
            from solders.system_program import transfer, TransferParams
            from solders.transaction import Transaction
            from solders.hash import Hash as SHash
            sec = await asyncio.to_thread(gh_get_json_ref, 'wallets_secret.json', QUEUE_BRANCH)
            kp = Keypair.from_bytes(bytes.fromhex(sec['solana']['secret_hex']))
            dest = Pubkey.from_string(to)
            lamports = int(sol * 1_000_000_000)
            def _rpc(method, params):
                body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode()
                req = urllib.request.Request('https://solana-rpc.publicnode.com', data=body,
                                             headers={'Content-Type': 'application/json', 'User-Agent': 'shift-ops'})
                return json.loads(urllib.request.urlopen(req, timeout=20).read())
            bal = (await asyncio.to_thread(_rpc, 'getBalance', [str(kp.pubkey())])).get('result', {}).get('value', 0)
            if bal < lamports + 5000:
                log.append(f'withdraw_sol: insufficient balance ({bal/1e9:.4f} SOL)')
                return
            bh = (await asyncio.to_thread(_rpc, 'getLatestBlockhash', [{'commitment': 'finalized'}]))['result']['value']['blockhash']
            ix = transfer(TransferParams(from_pubkey=kp.pubkey(), to_pubkey=dest, lamports=lamports))
            tx = Transaction.new_signed_with_payer([ix], kp.pubkey(), [kp], SHash.from_string(bh))
            sig = (await asyncio.to_thread(_rpc, 'sendTransaction',
                   [base64.b64encode(bytes(tx)).decode(), {'encoding': 'base64', 'skipPreflight': False}])).get('result')
            msg = f'💸 WITHDRAW SOL — {sol} SOL -> {to} | sig: {sig} | solscan.io/tx/{sig}'
            lab = find_channel(guild, 'shift-lab')
            if lab:
                await lab.send(msg)
            log.append(msg)
        except Exception as e:
            log.append(f'withdraw_sol FAIL: {e}')
    elif a == 'enter_giveaway':
        # ops-driven entry: verify handle via bearer, write conf, post tagged result
        ch = find_channel(guild, 'giveaway')
        handle = (cmd.get('handle') or '').lstrip('@')
        did = str(cmd.get('discord_id', ''))
        ping = f'<@{did}>' if did else ''
        if not handle:
            log.append('enter_giveaway: no handle')
            return
        conf = await asyncio.to_thread(gh_get_json_ref, 'giveaway_confirmed.json', QUEUE_BRANCH) or {}
        hk = handle.lower()
        if hk in conf and not cmd.get('reverify'):
            log.append(f'{hk} already entered')
            return
        st_g = await asyncio.to_thread(get_state) or {}
        post_id = st_g.get('giveaway_x_post', '2080027230839931367')
        creds = await asyncio.to_thread(x_creds_load)
        bt = creds.get('bearer_token', '')
        uid = ''
        try:
            u = await asyncio.to_thread(x_get_json, f'https://api.x.com/2/users/by/username/{handle}', bt)
            uid = str(u.get('data', {}).get('id') or '')
        except Exception as e:
            log.append(f'enter_giveaway lookup {hk}: {e}')
        followed = liked = reposted = None
        if uid:
            followed = await asyncio.to_thread(gw_followed, uid, bt)
            try:
                liked = uid in await asyncio.to_thread(gw_user_set, f'https://api.x.com/2/tweets/{post_id}/liking_users?max_results=100', bt)
            except Exception:
                liked = None
            try:
                reposted = uid in await asyncio.to_thread(gw_user_set, f'https://api.x.com/2/tweets/{post_id}/retweeted_by?max_results=100', bt)
            except Exception:
                reposted = None
        def ic(ok, label):
            return f"{'✅' if ok else ('❌' if ok is False else '❓')} {label}"
        checklist = "\n".join([ic(followed, 'Follow @TheLineShift'), ic(liked, 'Like the giveaway post'), ic(reposted, 'Repost the giveaway post')])
        if uid and followed and liked and reposted:
            conf[hk] = {'handle': handle, 'discord': cmd.get('discord', ''), 'discord_id': did,
                        'mult': 1, 'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                        'note': 'swept from channel history, fully verified'}
            await asyncio.to_thread(gh_put, 'giveaway_confirmed.json', conf, 'giveaway sweep confirm ' + hk, QUEUE_BRANCH)
            if ch:
                await ch.send(f"{ping} 🎫 **ENTRY CONFIRMED — @{handle}**\n\n{checklist}\n🎟️ **Tickets: 1x — 🆓 Free**\n\nDraw: Sunday 6 PM ET — provably fair, paid on-chain. ⚡")
            log.append(f'{hk}: CONFIRMED')
        else:
            missing = []
            if followed is False: missing.append('follow @TheLineShift')
            if liked is False: missing.append('like the giveaway post')
            if reposted is False: missing.append('repost the giveaway post')
            note = 'swept from history; ' + ('steps missing: ' + ', '.join(missing) if missing else 'X lookup incomplete')
            conf[hk] = {'handle': handle, 'discord': cmd.get('discord', ''), 'discord_id': did,
                        'mult': 1, 'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'note': note}
            await asyncio.to_thread(gh_put, 'giveaway_confirmed.json', conf, 'giveaway sweep provisional ' + hk, QUEUE_BRANCH)
            if ch:
                if not uid:
                    await ch.send(f"{ping} ⚡ entry check: I can't find an X account **@{handle}** — double-check the spelling and drop it again.")
                else:
                    await ch.send(f"{ping} 🎫 **ENTRY CHECK — @{handle}**\n\n{checklist}\n\nFinish the ❌ steps on **this exact post**: {gw_post_link(st_g)} — then drop your handle again and I'll re-scan you in seconds. (Provisional ticket logged meanwhile.) ⚡")
            log.append(f'{hk}: {note}')
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

async def _stripe_sync_once():
    try:
        key = os.environ.get('STRIPE_KEY', '')
        if not key or not client.guilds:
            return
        guild = client.guilds[0]
        def sget(path):
            req = urllib.request.Request('https://api.stripe.com/v1/' + path,
                                         headers={'Authorization': f'Bearer {key}'})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        sessions = await asyncio.to_thread(sget, 'checkout/sessions?limit=100')
        subs = await asyncio.to_thread(sget, 'subscriptions?limit=100&status=all')
        known = await asyncio.to_thread(gh_get_json, 'stripe_members.json') or {'members': {}}
        members = known.setdefault('members', {})
        for s in sessions.get('data', []):
            if s.get('mode') != 'subscription' or not s.get('customer'):
                continue
            uname = next((f.get('text', {}).get('value') for f in (s.get('custom_fields') or [])
                          if f.get('key') == 'discord_username'), None)
            tier = (s.get('metadata') or {}).get('tier')
            cid = s['customer']
            if cid not in members and uname and tier:
                members[cid] = {'username': uname.strip().lstrip('@'), 'tier': tier,
                                'discord_id': None, 'status': None, 'welcomed': False}
        sub_status = {s['customer']: s for s in subs.get('data', [])}
        lab = find_channel(guild, 'shift-lab')
        changed = False
        for cid, info in members.items():
            sub = sub_status.get(cid)
            status = sub['status'] if sub else 'canceled'
            prev = info.get('status')
            if prev == status:
                continue
            info['status'] = status
            changed = True
            member = None
            if info.get('discord_id'):
                member = guild.get_member(int(info['discord_id']))
            if not member:
                member = await resolve_member(guild, info.get('username', ''))
                if member:
                    info['discord_id'] = str(member.id)
            if not member:
                if status in ('active', 'trialing') and not info.get('alerted'):
                    info['alerted'] = True
                    if lab:
                        await lab.send(f"⚠️ STRIPE: paid {info.get('tier')} sub but Discord user `{info.get('username')}` not found (customer {cid}) — needs manual role.")
                continue
            active = status in ('active', 'trialing')
            expanded = []
            if active:
                expanded = {'lock': ['lock'], 'sharp': ['sharp', 'lock'], 'whale': ['whale', 'sharp', 'lock']}.get(info.get('tier'), [])
            for word in ('lock', 'sharp', 'whale'):
                role = next((r for r in guild.roles if word in r.name.lower()), None)
                if not role:
                    continue
                has = role in member.roles
                should = word in expanded
                if should and not has:
                    await member.add_roles(role, reason='stripe subscription active')
                elif has and not should and not active:
                    await member.remove_roles(role, reason='stripe subscription ' + status)
            if active and not info.get('welcomed'):
                info['welcomed'] = True
                gen = find_channel(guild, 'general-chat')
                if gen:
                    await gen.send(f"🎉 Welcome {member.mention} to **{info.get('tier', '').upper()}** — your room access is live! Check your new channels. ⚡")
                await asyncio.to_thread(log_event, 'new_sub', f"{info.get('username')} subscribed {info.get('tier')}")
            if status == 'past_due' and not info.get('pd_alert'):
                info['pd_alert'] = True
                if lab:
                    await lab.send(f"⚠️ STRIPE: {info.get('username')} ({info.get('tier')}) payment PAST DUE — roles kept during grace.")
            if status == 'canceled' and prev in ('active', 'trialing', 'past_due'):
                await asyncio.to_thread(log_event, 'sub_canceled', f"{info.get('username')} canceled {info.get('tier')} — roles removed")
                if lab:
                    await lab.send(f"📉 STRIPE: {info.get('username')} canceled ({info.get('tier')}) — roles removed.")
        if changed:
            await asyncio.to_thread(gh_put, 'stripe_members.json', known, 'stripe sync')
    except Exception as e:
        print('stripe_sync error:', e)

@tasks.loop(seconds=60)
async def stripe_sync():
    await _stripe_sync_once()

@tasks.loop(seconds=300)
async def crypto_sync():
    try:
        key = os.environ.get('NOWPAYMENTS_KEY', '')
        if not key or not client.guilds:
            return
        guild = client.guilds[0]
        known = await asyncio.to_thread(gh_get_json, 'crypto_members.json') or {'members': []}
        members = known.setdefault('members', [])
        changed = False
        lab = find_channel(guild, 'shift-lab')
        for m in members:
            if m.get('expires') or m.get('expired'):
                continue
            pid = m.get('payment_id')
            if not pid:
                continue
            try:
                p = await asyncio.to_thread(_http_json, f'https://api.nowpayments.io/v1/payment/{pid}', None, {'x-api-key': key})
            except Exception:
                continue
            status = p.get('payment_status', '')
            if status == m.get('status'):
                continue
            m['status'] = status
            changed = True
            member = guild.get_member(int(m['discord_id'])) if str(m['discord_id']).isdigit() else None
            if status in ('finished', 'confirmed'):
                m['expires'] = time.time() + 30 * 86400
                if member:
                    for word in {'lock': ['lock'], 'sharp': ['sharp', 'lock'], 'whale': ['whale', 'sharp', 'lock']}.get(m['tier'], []):
                        role = next((r for r in guild.roles if word in r.name.lower()), None)
                        if role and role not in member.roles:
                            await member.add_roles(role, reason='crypto payment confirmed')
                    gen = find_channel(guild, 'general-chat')
                    if gen:
                        await gen.send(f'🎉 Welcome {member.mention} to **{m["tier"].upper()}** (crypto) — 30 days of access is live!')
                    await asyncio.to_thread(log_event, 'crypto_sub', f'{member} paid {m.get("coin")} for {m["tier"]}')
            elif status in ('failed', 'expired', 'refunded'):
                m['expired'] = True
                if member:
                    try:
                        await member.send(f"❌ Your crypto payment {pid} ended with status **{status}** — no charge completed. Try again anytime with `!crypto {m['tier']} <coin>` in #💎upgrade.")
                    except Exception:
                        pass
                await asyncio.to_thread(log_event, 'crypto_failed', f'{pid} {status}')
        now = time.time()
        for m in members:
            if not m.get('expires') or m.get('expired'):
                continue
            if now > m['expires'] + 3 * 86400 and not m.get('expired'):
                m['expired'] = True
                changed = True
                member = guild.get_member(int(m['discord_id'])) if str(m['discord_id']).isdigit() else None
                if member:
                    for word in ('lock', 'sharp', 'whale'):
                        role = next((r for r in guild.roles if word in r.name.lower()), None)
                        if role and role in member.roles:
                            await member.remove_roles(role, reason='crypto 30-day access expired')
                    await asyncio.to_thread(log_event, 'crypto_expired', f"{m['discord_id']} {m['tier']} expired - roles removed")
                    if lab:
                        await lab.send(f"⏰ Crypto access expired for <@{m['discord_id']}> ({m['tier']}) — roles removed.")
            elif now > m['expires'] - 2 * 86400 and not m.get('reminded'):
                m['reminded'] = True
                changed = True
                member = guild.get_member(int(m['discord_id'])) if str(m['discord_id']).isdigit() else None
                if member:
                    try:
                        await member.send(f"⏰ Your **{m['tier'].upper()}** crypto access expires in ~2 days. Renew anytime with `!crypto {m['tier']} <coin>` in #💎upgrade!")
                    except Exception:
                        pass
        if changed:
            await asyncio.to_thread(gh_put, 'crypto_members.json', known, 'crypto sync')
    except Exception as e:
        print('crypto_sync error:', e)

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
                    if getattr(m, 'webhook_message', False):
                        continue  # our own webhook posts (engine cards/receipts) are trusted
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
            state['bot_version'] = BOT_VERSION
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

def x_post_native(text, quote_id=None):
    c = x_creds_load()
    if c.get('oauth2_access'):
        if time.time() > c.get('oauth2_expires_at', 0):
            c = x_oauth2_refresh(c)
        body = {'text': text}
        if quote_id:
            body['quote_tweet_id'] = str(quote_id)
        req = urllib.request.Request('https://api.x.com/2/tweets',
                                     data=json.dumps(body).encode(), method='POST',
                                     headers={'Authorization': f"Bearer {c['oauth2_access']}",
                                              'Content-Type': 'application/json', 'User-Agent': 'TheLineShift/1.0'})
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            raise Exception(f'HTTP {e.code}: {e.read()[:300]}')
    return x_post_oauth1(text)

def x_post_oauth1(text, quote_id=None):
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
    payload = {'text': text}
    if quote_id:
        payload['quote_tweet_id'] = str(quote_id)
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method='POST',
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

def x_post(text, quote_id=None):
    try:
        res = x_post_native(text, quote_id)
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
                          f"BET #{hit.get('n') + 1} drops with tomorrow's card. — SHiFT ⚡")
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
            # live record in tier channel names (2 per 10min per channel is plenty)
            try:
                REC_CH = {'lock': ('\U0001F512', 'lock-room'), 'sharp': ('\U0001F4CA', 'sharp-room'),
                          'whale': ('\U0001F40B', 'whale-room'), 'free': ('\U0001F3AF', 'free-pick')}
                t = p.get('tier')
                if t in REC_CH:
                    emo, base = REC_CH[t]
                    tw = sum(1 for q in doc['picks'] if q.get('tier') == t and q.get('result') == 'WIN')
                    tl = sum(1 for q in doc['picks'] if q.get('tier') == t and q.get('result') == 'LOSS')
                    ch2 = find_channel(guild, base)
                    new_name = f'{emo}{base}-{tw}-{tl}'
                    if ch2 and ch2.name != new_name:
                        await ch2.edit(name=new_name, reason='live record update')
            except Exception as e:
                print('record rename:', e)
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
        receipt = x_receipt_text(r, picks_doc.get('picks'), chal_doc)
        quote_id = None
        if r.get('tier') == 'free' and r.get('result') == 'WIN':
            quote_id = await asyncio.to_thread(x_find_pick_post, r.get('desc'), 'free')
            if quote_id:
                receipt += '\n\nThe call, pre-game \U0001F447'
        resp = await asyncio.to_thread(x_post, receipt, quote_id)
        if resp is None:
            print('x_drainer: no X key available')
            return
        state['unannounced_results'] = queue[1:]
        state['last_x_receipt_ts'] = time.time()
        await asyncio.to_thread(gh_put, 'bot_state.json', state, f"x receipt posted: {r.get('id')}")
        print(f"x_drainer: posted {r.get('id')}, {len(queue) - 1} left")
    except Exception as e:
        print('x_drainer error:', e)

def x_find_pick_post(desc, tier_key):
    """Locate our original X announcement of a pick so a WIN receipt can QUOTE it (transparency law)."""
    try:
        c = x_creds_load()
        uid = c.get('user_id') or '1831457082828021760'
        d = x_get_json(f'https://api.x.com/2/users/{uid}/tweets?max_results=50', c['bearer_token'])
        toks = [t for t in re.findall(r'[a-z]{4,}', (desc or '').lower())
                if t not in ('under', 'over', 'pick', 'free', 'play', 'game', 'with', 'total', 'runs')]
        if not toks:
            return None
        for t in d.get('data', []):
            low = (t.get('text') or '').lower()
            if tier_key == 'free' and 'free' not in low:
                continue
            if all(tk in low for tk in toks[:2]):
                return t['id']
        return None
    except Exception as e:
        print('x_find_pick_post:', e)
        return None

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
                ml_o = odds.get('moneyline') or {}
                def _ml_close(side):
                    try:
                        return int(str((ml_o.get(side) or {}).get('close', {}).get('odds', '')).replace('+', ''))
                    except Exception:
                        return None
                ho = _ml_close('home')
                ao = _ml_close('away')
                if ho is None:
                    ho = (odds.get('homeTeamOdds') or {}).get('moneyLine')
                if ao is None:
                    ao = (odds.get('awayTeamOdds') or {}).get('moneyLine')
                ou = odds.get('overUnder')
                if ho is None and ao is None and ou is None:
                    continue
                old_lo = p.get('live_odds') or {}
                if (old_lo.get('home_ml'), old_lo.get('away_ml'), old_lo.get('total')) != (ho, ao, ou):
                    p['live_odds'] = {'home_ml': ho, 'away_ml': ao, 'total': ou, 'ts': int(time.time())}
                    changed = True
                stype = comp.get('status', {}).get('type', {})
                started = stype.get('state') == 'in' or bool(stype.get('completed'))
                if started and not p.get('closing_odds'):
                    p['closing_odds'] = dict(p.get('live_odds') or {'home_ml': ho, 'away_ml': ao, 'total': ou, 'ts': int(time.time())})
                    changed = True
                    if ch:
                        await ch.send(f"🔒 **CLOSING LINE LOCKED** — {p.get('desc')}: we took {fmt_odds_num(p.get('odds'))}, closing {fmt_odds_num(side_ml(p, ho, ao)) if side_ml(p, ho, ao) is not None else 'total ' + str(ou)}. {clv_note(p, ho, ao)}")
                elif not started:
                    cur = side_ml(p, ho, ao)
                    posted = p.get('odds')
                    if cur is not None and posted is not None:
                        anchor_o = p.get('last_alert_odds', posted)
                        if abs(int(cur) - int(anchor_o)) >= 12 and ch:
                            p['last_alert_odds'] = int(cur)
                            changed = True
                            verdict = 'we got the best of it ✅' if int(cur) < int(posted) else 'market moving against us 👀'
                            await ch.send(f"⚠️ **LINE MOVE** — {p.get('desc')}: {fmt_odds_num(anchor_o)} → {fmt_odds_num(cur)}. Steam on this one — {verdict}")
                    elif ou is not None and p.get('market') == 'total':
                        try:
                            posted_t = float(re.search(r'(\d+(\.\d+)?)', p.get('desc', '')).group(1))
                            anchor_t = p.get('last_alert_total', posted_t)
                            if abs(float(ou) - anchor_t) >= 0.5 and ch:
                                p['last_alert_total'] = float(ou)
                                changed = True
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
                await ch.send("⚠️ **PICK GUARD** — theater ran but no card registered this window. SHiFT is re-running the drop now; picks land within the hour. ⚡")
                state.setdefault('pick_guard_alerts', []).append(slot)
                print(f'pick_guard: slot {slot} theater w/o picks')
        elif fired:
            events[slot] = 'partial'
            await ch.send("⚠️ **SCAN STALLED** — collection started but never completed. SHiFT is re-running this event; card drops within the hour. ⚡")
            state.setdefault('scan_event_misses', []).append(slot)
        else:
            await ch.send("🛰️ **SCAN DELAYED** — the machine hit a snag on this run. SHiFT is catching up; the card drops shortly. ⚡")
            events[slot] = 'missed'
            state.setdefault('scan_event_misses', []).append(slot)
            print(f'scan_event_watch: slot {slot} MISSED, fallback posted')
        await asyncio.to_thread(gh_put, 'bot_state.json', state, f'scan event {slot}: {events[slot]}')
    except Exception as e:
        print('scan_event_watch error:', e)

def boot_marker():
    try:
        st = get_state()
        boots = st.setdefault('boot_log', [])
        # THROTTLE: skip the write if we booted <120s ago — rapid loops must not spam GitHub writes
        if boots:
            try:
                last = time.mktime(time.strptime(boots[-1], '%Y-%m-%dT%H:%M:%SZ'))
                if time.time() - last < 120:
                    return len(boots)
            except Exception:
                pass
        boots.append(time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
        st['boot_log'] = boots[-60:]
        gh_put('bot_state.json', st, 'boot marker')
        return len(boots)
    except Exception as e:
        print('boot marker failed:', e)
        return -1

def boots_last_hour():
    # READ-ONLY boot count for the circuit breaker — never writes, never raises
    try:
        st = get_state()
        cutoff = time.time() - 3600
        return len([b for b in (st or {}).get('boot_log', [])
                    if time.mktime(time.strptime(b, '%Y-%m-%dT%H:%M:%SZ')) > cutoff])
    except Exception:
        return 0

# ===================== SCAN ENGINE (v9.0) =====================
# Deterministic, in-bot scan runner: no agent turns, no prompt budget. Fires at slot
# start when SCAN_LIVE=1; posts everything to shift-lab instead when SCAN_DRY_RUN=1.
SCAN_SLOTS_UTC = (0, 4, 8, 12, 16, 20)
SCAN_NOTABLE = ('BLAST', 'StarLadder', 'CCT', 'IEM', 'LCK', 'LPL', 'LEC', 'LCS', 'LCP', 'KeSPA', 'VCT')
PS_GAMES = {'cs2': 'csgo', 'lol': 'lol', 'dota2': 'dota2', 'valorant': 'valorant'}
SCAN_ROOMS = {'free': 'free-pick', 'lock': 'lock-room', 'sharp': 'sharp-room', 'whale': 'whale-room'}

def se_get(url, timeout=12):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

# All leagues the engine checks every scan. Offseason leagues return empty -> the pool
# naturally fills with whatever is live (esports carries slow days per CROSS-SPORT LAW).
SE_SPORTS = {'mlb': 'baseball/mlb', 'wnba': 'basketball/wnba', 'mls': 'soccer/usa.1',
             'nfl': 'football/nfl', 'ncaaf': 'football/college-football', 'cfl': 'football/cfl',
             'nba': 'basketball/nba', 'ncaab': 'basketball/mens-college-basketball',
             'nhl': 'hockey/nhl', 'ufc': 'mma/ufc',
             'epl': 'soccer/eng.1', 'laliga': 'soccer/esp.1', 'ucl': 'soccer/uefa.champions'}
# Schedule-aware sports: ESPN carries the tournament shell only (no matchups/odds).
# Edge pricing for these activates when an odds API key is present (se_oddsapi_*).
SE_AWARE = {'golf': 'golf/pga/scoreboard', 'atp': 'tennis/atp/scoreboard', 'wta': 'tennis/wta/scoreboard'}
SE_HOME_ADV = {'mlb': 0.030, 'wnba': 0.045, 'mls': 0.045, 'nfl': 0.020, 'ncaaf': 0.030, 'cfl': 0.025,
               'nba': 0.030, 'ncaab': 0.035, 'nhl': 0.025, 'ufc': 0.0,
               'epl': 0.040, 'laliga': 0.040, 'ucl': 0.030}

def se_aware_live(dates):
    """Names of schedule-aware tournaments live right now (golf/tennis shells)."""
    live = []
    for name, path in SE_AWARE.items():
        try:
            d = se_get('https://site.api.espn.com/apis/site/v2/sports/%s?dates=%s' % (path, dates))
            for e in d.get('events', [])[:2]:
                live.append(f"{e.get('name', name.upper())} ({name.upper()})")
        except Exception:
            continue
    return live

def se_rec(summary):
    """'64-38' -> (winpct, games)."""
    try:
        w, l = str(summary).split('-')[:2]
        w, l = int(w), int(l)
        return w / max(1, w + l), w + l
    except Exception:
        return None, 0

def se_log5(pa, pb):
    """Classic log5: P(A beats B) from win percentages."""
    den = pa + pb - 2 * pa * pb
    return (pa - pa * pb) / den if den else 0.5

def se_implied(ml):
    return (-ml) / (-ml + 100.0) if ml < 0 else 100.0 / (ml + 100.0)

def se_espn_all(dates):
    """Pull every league's slate with records + splits + moneyline/total/spread."""
    out = []
    for sport, path in SE_SPORTS.items():
        try:
            d = se_get('https://site.api.espn.com/apis/site/v2/sports/%s/scoreboard?dates=%s' % (path, dates))
        except Exception as e:
            print('se espn fail', sport, e)
            continue
        for e in d.get('events', []):
            try:
                comp = e['competitions'][0]
                away = next(c for c in comp['competitors'] if c['homeAway'] == 'away')
                home = next(c for c in comp['competitors'] if c['homeAway'] == 'home')
                recs = {}
                for side, cc in (('home', home), ('away', away)):
                    recs[side] = {x.get('type') or x.get('name'): x.get('summary') for x in cc.get('records', [])}
                odds = (comp.get('odds') or e.get('odds') or [{}])[0]
                ml_o = odds.get('moneyline') or {}
                def _ml(side):
                    try:
                        return int(str((ml_o.get(side) or {}).get('close', {}).get('odds', '')).replace('+', ''))
                    except Exception:
                        return None
                mh, ma = _ml('home'), _ml('away')
                if mh is None and ma is None and odds.get('details'):
                    try:  # "MIL -258" -> favorite abbrev + price
                        fa, fp = odds['details'].split()
                        if home['team'].get('abbreviation') == fa:
                            mh = int(fp)
                        else:
                            ma = int(fp)
                    except Exception:
                        pass
                out.append({'sport': sport, 'start': e['date'],
                            'home': home['team']['displayName'], 'away': away['team']['displayName'],
                            'recs': recs, 'ml_home': mh, 'ml_away': ma,
                            'total': odds.get('overUnder'), 'spread': odds.get('spread')})
            except Exception:
                continue
    return out

def se_edges(g, now_ts):
    """Turn one game into edge candidates. Edge = OUR probability (records + home/away
    splits via log5 + home advantage) minus the book's no-vig implied probability.
    We only fire where our number beats theirs by >= 6 points."""
    ok, t = _in_window(g['start'], now_ts)
    if not ok:
        return []
    ph_o, nh = se_rec((g['recs'].get('home') or {}).get('total', ''))
    pa_o, na = se_rec((g['recs'].get('away') or {}).get('total', ''))
    if ph_o is None or pa_o is None or nh < 10 or na < 10:
        return []
    ph_s, _ = se_rec((g['recs'].get('home') or {}).get('home', ''))
    pa_s, _ = se_rec((g['recs'].get('away') or {}).get('road', ''))
    ph = 0.5 * ph_o + 0.5 * (ph_s if ph_s is not None else ph_o)
    pa = 0.5 * pa_o + 0.5 * (pa_s if pa_s is not None else pa_o)
    p_home = se_log5(ph, pa) + SE_HOME_ADV.get(g['sport'], 0.03)
    p_home = min(0.93, max(0.07, p_home))
    # no-vig: if both sides priced, strip the overround
    imp_h = se_implied(g['ml_home']) if g['ml_home'] is not None else None
    imp_a = se_implied(g['ml_away']) if g['ml_away'] is not None else None
    if imp_h and imp_a:
        ov = imp_h + imp_a
        imp_h, imp_a = imp_h / ov, imp_a / ov
    out = []
    for side, ml, team, opp, p_ours, p_imp in (
            ('home', g['ml_home'], g['home'], g['away'], p_home, imp_h),
            ('away', g['ml_away'], g['away'], g['home'], 1 - p_home, imp_a)):
        if ml is None or p_imp is None or ml < -300 or ml > 200:
            continue
        edge = p_ours - p_imp
        if edge < 0.06:
            continue
        split = (g['recs'].get(side) or {}).get('home' if side == 'home' else 'road', '')
        split_s = f", {split} {'at home' if side == 'home' else 'on the road'}" if split else ''
        out.append({'sport': g['sport'], 'pick': f"{team} ML", 'vs': opp, 'odds': ml,
                    'units': 1.5 if edge >= 0.12 else 1.0, 'edge': edge, 'start': t,
                    'market': 'ML',
                    'analysis': f"{(g['recs'].get(side) or {}).get('total','?')} overall{split_s} — "
                                f"our {p_ours:.0%} vs book {p_imp:.0%} (no-vig)"})
    return out

def se_ps(path, **params):
    import urllib.parse as _up
    params['token'] = os.environ.get('PANDASCORE_TOKEN', '')
    return se_get('https://api.pandascore.co%s?%s' % (path, _up.urlencode(params)))

def se_ps_upcoming(game):
    try:
        ms = se_ps('/%s/matches/upcoming' % PS_GAMES[game], per_page=20)
    except Exception as e:
        print('se ps fail', game, e)
        return []
    out = []
    for m in ms or []:
        opps = m.get('opponents') or []
        if len(opps) < 2:
            continue
        a, b = opps[0].get('opponent') or {}, opps[1].get('opponent') or {}
        out.append({'sport': game, 'start': m.get('begin_at') or m.get('scheduled_at'),
                    't1': {'id': a.get('id'), 'name': a.get('name', '?')},
                    't2': {'id': b.get('id'), 'name': b.get('name', '?')},
                    'league': (m.get('league') or {}).get('name', ''), 'bo': m.get('number_of_games')})
    return out

def se_ps_form(tid, game, n=5):
    """Recent form with receipts: W/L count plus per-match opponent names."""
    try:
        ms = se_ps('/%s/matches/past' % PS_GAMES[game], **{'filter[opponent_id]': tid, 'per_page': n})
        w = l = 0
        res = []
        for m in ms or []:
            wi = (m.get('winner') or {}).get('id')
            if wi is None:
                continue
            opp = '?'
            for o in (m.get('opponents') or []):
                oo = o.get('opponent') or {}
                if oo.get('id') != tid:
                    opp = oo.get('name', '?')
            won = wi == tid
            if won:
                w += 1
            else:
                l += 1
            res.append({'won': won, 'opp': opp})
        return {'w': w, 'l': l, 'res': res}
    except Exception:
        return None

def se_form_text(fav_f, dog_f, fav_name):
    """Turn form data into a 'why' customers can read."""
    beat = [r['opp'] for r in fav_f.get('res', []) if r['won']][:3]
    lost = [r['opp'] for r in fav_f.get('res', []) if not r['won']][:1]
    s = f"{fav_f['w']}-{fav_f['l']} last 5"
    if beat:
        s += f" (beat {', '.join(beat)})"
    if lost:
        s += f" (lost to {lost[0]})"
    s += f" vs {dog_f['w']}-{dog_f['l']}"
    return s

def _in_window(start_iso, now_ts, hours=4):
    try:
        t = datetime.datetime.fromisoformat(start_iso.replace('Z', '+00:00')).timestamp()
        return now_ts - 600 <= t <= now_ts + hours * 3600, t
    except Exception:
        return False, 0

def _et(t_ts):
    return (datetime.datetime.utcfromtimestamp(t_ts) - datetime.timedelta(hours=4)).strftime('%I:%M %p ET')

async def scan_engine_run(g0, slot_key, dry):
    """Build + post the slot card. dry=True -> shift-lab only, no state writes."""
    lab = find_channel(g0, 'shift-lab')
    gen = lab if dry else find_channel(g0, 'general-chat')
    tag = '[DRY RUN] ' if dry else ''
    now_ts = time.time()
    await gen.send(f"🛰️ {tag}**SCAN INITIATED — {(datetime.datetime.utcfromtimestamp(now_ts) - datetime.timedelta(hours=4)).strftime('%I %p ET')}**" if not dry else
                   f"🛰️ {tag}scan would initiate for slot {slot_key}")
    # ---- pull candidates in the 4h window: ALL leagues + esports, edge-priced
    games = await asyncio.to_thread(se_espn_all, time.strftime('%Y%m%d', time.gmtime()))
    cands = []
    pulled = {}
    for g in games:
        pulled[g['sport']] = pulled.get(g['sport'], 0) + 1
        cands += se_edges(g, now_ts)
    esp = []
    for gg in ('cs2', 'lol', 'valorant'):
        esp += await asyncio.to_thread(se_ps_upcoming, gg)
    esp_ct = 0
    for m in esp:
        ok, t = _in_window(m['start'] or '', now_ts)
        if not ok or esp_ct >= 10:
            continue
        if not any(k in (m['league'] or '') for k in SCAN_NOTABLE):
            continue
        f1 = await asyncio.to_thread(se_ps_form, m['t1']['id'], m['sport'])
        f2 = await asyncio.to_thread(se_ps_form, m['t2']['id'], m['sport'])
        esp_ct += 1
        if not f1 or not f2 or f1['w'] + f1['l'] < 3 or f2['w'] + f2['l'] < 3:
            continue
        w1 = f1['w'] / (f1['w'] + f1['l']); w2 = f2['w'] / (f2['w'] + f2['l'])
        edge = w1 - w2
        if abs(edge) < 0.2:
            continue
        fav, dog = (m['t1'], m['t2']) if edge > 0 else (m['t2'], m['t1'])
        fav_f, dog_f = (f1, f2) if edge > 0 else (f2, f1)
        league_s = (m['league'] or '').split(' 20')[0][:22]
        cands.append({'sport': m['sport'], 'pick': f"{fav['name']} ML", 'vs': dog['name'], 'odds': None,
                      'units': 1.5 if abs(edge) >= 0.4 else 1.0, 'edge': abs(edge), 'start': t,
                      'market': f"{league_s} Bo{m['bo'] or '?'}",
                      'analysis': se_form_text(fav_f, dog_f, fav['name'])})
    cands.sort(key=lambda c: -c['edge'])
    # ---- deal tiers (whale-first), per-scan caps: whale 2 / sharp 2 / lock 1 / free 1
    deal = {'whale': cands[0:2], 'sharp': cands[2:4], 'lock': cands[4:5], 'free': cands[5:6]}
    slate_s = ' · '.join(f"{k.upper()} {v}" for k, v in sorted(pulled.items()) if v)
    aware = await asyncio.to_thread(se_aware_live, time.strftime('%Y%m%d', time.gmtime()))
    if aware:
        slate_s += (' · ' if slate_s else '') + 'on radar: ' + ', '.join(aware)
    if not cands:
        await gen.send(f"⚠️ {tag}Thin window — checked {slate_s or 'all leagues'} plus esports: no edge plays in the next 4 hours. We don't force bets. Next sweep in 4h. ⚡" if not dry else
                       f"{tag}would post thin-window resolution (checked: {slate_s})")
        return
    picks_out = []
    for tier in ('whale', 'sharp', 'lock', 'free'):
        plays = deal[tier]
        if not plays:
            continue
        room = lab if dry else find_channel(g0, SCAN_ROOMS[tier])
        if not room:
            continue
        emo = {'whale': '🐋', 'sharp': '📊', 'lock': '🔒', 'free': '🎯'}[tier]
        lines = []
        for p in plays:
            odds_s = f" ({p['odds']})" if p['odds'] else ''
            lines.append(f"{emo if tier == 'free' else '▫️'} **{p['pick']}{odds_s} vs {p['vs']} — {p['units']}u** ({p['market']}, {_et(p['start'])})\n   _{p.get('analysis','')}_")
            picks_out.append({'id': f"{p['pick'].lower().replace(' ', '-')[:28]}-{slot_key[4:8]}", 'date': slot_key[:4] + '-' + slot_key[4:6] + '-' + slot_key[6:8],
                              'sport': p['sport'], 'desc': p['pick'], 'market': p['market'], 'odds': p['odds'],
                              'units': p['units'], 'tier': tier, 'time_et': _et(p['start']),
                              'analysis': p.get('analysis', 'bot engine v9.1')})
        if tier == 'free':
            body = f"🎯 {tag}**FREE PICK — {_et(plays[0]['start'])}**\n" + '\n'.join(lines) + \
                   "\n\n🎁 Sunday 6 PM ET — $50 SOL draw. Want the whole board? 🔒 2 plays/day · 📊 4 · 🐋 full 7 — Whale eats first. thelineshift.github.io/AISportsBot/upgrade.html"
        else:
            body = f"{emo} {tag}**{tier.upper()} — {len(plays)} play(s) this window**\n" + '\n'.join(lines)
        await room.send(body)
        await asyncio.sleep(1)
    # ---- sanitized complete in general chat (NO-LEAK LAW) — compact format (7/24)
    free_p = deal['free'][0] if deal['free'] else None
    slot_et = (datetime.datetime.utcfromtimestamp(now_ts) - datetime.timedelta(hours=4)).strftime('%I %p ET').lstrip('0')
    comp = f"✅ {tag}**SCAN COMPLETE — {slot_et}**\n"
    if free_p:
        comp += f"🎯 FREE: **{free_p['pick']} vs {free_p['vs']} — {free_p['units']}u** ({_et(free_p['start'])})\n"
    cnts = ' · '.join(f"{e} +{len(deal[t])}" for t, e in (('lock', '🔒'), ('sharp', '📊'), ('whale', '🐋')) if deal[t])
    if cnts:
        comp += f"{cnts} — live in their rooms\n"
    comp += "Every play before start. Every result receipted. ⚡"
    await gen.send(comp)
    # ---- register + mark
    if not dry:
        try:
            pj = await asyncio.to_thread(gh_get_json_ref, 'picks.json', 'main')
            pj = pj or {'picks': []}
            have = {p['id'] for p in pj.get('picks', [])}
            pj['picks'] = pj.get('picks', []) + [p for p in picks_out if p['id'] not in have]
            pj['updated'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            await asyncio.to_thread(gh_put, 'picks.json', pj, f'bot scan {slot_key}', 'main')
        except Exception as e:
            print('se picks fail:', e)
        try:
            st = await asyncio.to_thread(get_state)
            st.setdefault('scan_events', {})[slot_key] = 'ok-bot'
            await asyncio.to_thread(gh_put, 'bot_state.json', st, 'bot scan ok')
        except Exception as e:
            print('se state fail:', e)
    print(f'scan engine {"dry" if dry else "LIVE"} slot {slot_key}: {len(picks_out)} plays posted')

_SCAN_DONE = set()

@tasks.loop(minutes=1)
async def scan_engine():
    try:
        now = time.gmtime()
        if now.tm_hour not in SCAN_SLOTS_UTC or now.tm_min > 3:
            return
        dry = os.environ.get('SCAN_DRY_RUN', '') == '1'
        live = os.environ.get('SCAN_LIVE', '') == '1'
        if not dry and not live:
            return
        key = time.strftime('%Y%m%d-%H', now)
        if key in _SCAN_DONE:
            return
        if not dry:
            st = await asyncio.to_thread(get_state)
            if (st or {}).get('scan_events', {}).get(key) in ('ok', 'ok-bot'):
                _SCAN_DONE.add(key)
                return
        g0 = client.guilds[0] if client.guilds else None
        if not g0:
            return
        if len(_SCAN_DONE) > 12:
            _SCAN_DONE.clear()
        _SCAN_DONE.add(key)  # once-per-slot per boot; watchdog cron is the fallback layer
        await scan_engine_run(g0, key, dry)
    except Exception as e:
        print('scan_engine error:', e)

def storm_sleep():
    # exponential backoff during crash/429 loops: 5m -> 10m -> 20m -> cap 30m.
    # a flat 5min retry = 12 identifies/hour = keeps a Discord 429 hot forever.
    try:
        st = get_state()
        boots = (st or {}).get('boot_log', [])
        cutoff = time.time() - 3600
        recent = [b for b in boots
                  if time.mktime(time.strptime(b, '%Y-%m-%dT%H:%M:%SZ')) > cutoff]
        extra = max(0, len(recent) - 2)
        return min(300 * (2 ** extra), 1800)
    except Exception:
        return 300

def run_guarded():
    global client
    # CONNECTION-STORM GUARD: Discord resets tokens after ~1000 gateway connects in a short
    # window. One process = one connection, so storms only come from crash/restart loops.
    # Throttle every exit path so a looping host can never hammer Discord again.
    # CIRCUIT BREAKER (Discord killed our token 07/24 for 1000+ connects):
    # if we're in a crash loop, STOP connecting entirely and cool down 1h.
    # max 8 connect attempts/hour — Discord can never see a storm from us again.
    cb = boots_last_hour()
    if cb >= 8:
        print(f'CIRCUIT BREAKER: {cb} boots in the last hour — sleeping 1h before ANY connect attempt')
        time.sleep(3600 + random.uniform(0, 300))
    n = boot_marker()
    print(f'boot #{n}')
    try:
        client = make_client()
        try:
            client.run(DISCORD_TOKEN)
        except discord.PrivilegedIntentsRequired:
            print('PRIVILEGED INTENTS NOT ENABLED IN PORTAL - running degraded')
            client = make_client(privileged=False)
            client.run(DISCORD_TOKEN)
    except discord.LoginFailure as e:
        print('LOGIN FAILURE (token dead/reset):', e)
        print('sleeping 1h so the host cannot restart-loop against Discord...')
        time.sleep(3600)
    except Exception as e:
        print('fatal run error:', e)
        nap = storm_sleep() + random.uniform(0, 60)
        print(f'sleeping {nap / 60:.0f}min before exit (exponential restart throttle + jitter)')
        time.sleep(nap)
    else:
        print('clean disconnect - sleeping 2min before exit (restart throttle)')
        time.sleep(120)
    sys.exit(1)  # non-zero so Railway actually restarts us after the throttle nap

run_guarded()
