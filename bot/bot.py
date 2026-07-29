import os, json, time, base64, asyncio, urllib.request, random, re, datetime
import discord
from discord.ext import tasks

DISCORD_TOKEN = os.environ['DISCORD_BOT_TOKEN']
GH_TOKEN = os.environ.get('GITHUB_TOKEN', '')
REPO = 'thelineshift/SHiFTS'
QUEUE_BRANCH = 'commands'
RAW = f'https://raw.githubusercontent.com/{REPO}/{QUEUE_BRANCH}'
API = f'https://api.github.com/repos/{REPO}/contents'

TIER_ROLES = {'\U0001F512 Lock Room': 'lock', '\U0001F4CA Sharp': 'sharp', '\U0001F40B Whale': 'whale'}

BOT_NICK = '⚡ SHiFT'
BOT_STATUS = 'the board 🛰️'
BOT_VERSION = '9.24.19'  # COMEBACK LAW: pre-match anchors + set-tree floors buy favorites down a set/map; esports 95c tails banned; caps 15%/20%

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



CRYPTO_TIERS = {'lock': 29.99, 'sharp': 49.99, 'whale': 99.99}

def _http_json(url, payload=None, headers=None, timeout=20):
    h = {'Content-Type': 'application/json',
         'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) shift-ops/1.0'}  # public RPCs 403 without UA
    if headers:
        h.update(headers)
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=h, method='POST' if data else 'GET')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

EVM_RPCS = {
    'ethereum': ('ETH', 'ethereum', ['https://ethereum-rpc.publicnode.com', 'https://ethereum.public.blockpi.network/v1/rpc/public', 'https://eth.merkle.io']),
    'base': ('ETH', 'ethereum', ['https://mainnet.base.org', 'https://base-rpc.publicnode.com', 'https://base.public.blockpi.network/v1/rpc/public']),
    'polygon': ('POL', 'polygon-ecosystem-token', ['https://polygon-bor-rpc.publicnode.com', 'https://polygon.drpc.org']),
    'bsc': ('BNB', 'binancecoin', ['https://bsc-dataseed.binance.org', 'https://bsc-rpc.publicnode.com', 'https://1rpc.io/bnb']),
    'arbitrum': ('ETH', 'ethereum', ['https://arb1.arbitrum.io/rpc', 'https://arbitrum-one-rpc.publicnode.com', 'https://1rpc.io/arb']),
}
USDC_E_POLYGON = '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174'  # bridged USDC.e — the Polymarket rail
USDC_POLYGON = '0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359'    # native USDC

ANKR_CHAIN = {'ethereum': 'eth', 'base': 'base', 'polygon': 'polygon', 'bsc': 'bsc', 'arbitrum': 'arbitrum'}

def _evm_call(chain, method, params):
    """Call a JSON-RPC method on `chain`: Ankr (keyed) first, then public endpoints. Requires a real 'result'."""
    last = 'no rpc configured'
    ankr = os.environ.get('ANKR_KEY', '')
    if ankr and chain in ANKR_CHAIN:
        for path in (ANKR_CHAIN[chain], 'multichain'):
            try:
                b = _http_json(f'https://rpc.ankr.com/{path}/{ankr}',
                               {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}, timeout=12)
                if isinstance(b, dict) and b.get('result') is not None:
                    return b['result']
                last = str((b or {}).get('error') or b)[:80]
            except Exception as e:
                last = str(e)[:80]
    for rpc in EVM_RPCS[chain][2]:
        try:
            b = _http_json(rpc, {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
            if isinstance(b, dict) and b.get('result') is not None:
                return b['result']
            last = str((b or {}).get('error') or b)[:80]
        except Exception as e:
            last = str(e)[:80]
    raise RuntimeError(last)

def _erc20_balance(chain, token, addr):
    data = '0x70a08231' + '0' * 24 + addr[2:].lower()
    return int(_evm_call(chain, 'eth_call', [{'to': token, 'data': data}, 'latest']), 16) / 1e6

BLOCKSCOUT = {
    'ethereum': 'https://eth.blockscout.com',
    'base': 'https://base.blockscout.com',
    'polygon': 'https://polygon.blockscout.com',
}

def _bs_get(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) shift-ops/1.0'})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)

def _evm_native(chain, addr):
    """Native balance + usd. Tries Ankr (if ANKR_KEY set) -> Blockscout -> public RPCs.
    Returns (balance, price_or_None). Raises the last error if every source fails."""
    last = 'no source'
    ankr = os.environ.get('ANKR_KEY', '')
    if ankr:
        try:
            b = _http_json(f'https://rpc.ankr.com/{ANKR_CHAIN.get(chain, "multichain")}/{ankr}',
                           {'jsonrpc': '2.0', 'id': 1, 'method': 'eth_getBalance', 'params': [addr, 'latest']}, timeout=12)
            if b.get('result') is not None:
                return int(b['result'], 16) / 1e18, None
            last = str(b.get('error') or b)[:80]
        except Exception as e:
            last = str(e)[:80]
    bs = BLOCKSCOUT.get(chain)
    if bs:
        try:
            r = _bs_get(f'{bs}/api/v2/addresses/{addr}')
            rate = float(r.get('exchange_rate') or 0) or None
            return int(r.get('coin_balance') or 0) / 1e18, rate
        except Exception as e:
            last = str(e)[:80]
    try:
        return int(_evm_call(chain, 'eth_getBalance', [addr, 'latest']), 16) / 1e18, None
    except Exception as e:
        last = str(e)[:80]
    raise RuntimeError(last)

def _evm_usdc_polygon(addr):
    """USDC.e + native USDC on Polygon: Blockscout token balances, else eth_call."""
    syms = {'0x2791bca1f2de4661ed88a30c99a7a9449aa84174', '0x3c499c542cef5e3811e1192ce70d8cc03d5c3359'}
    try:
        toks = _bs_get(f'{BLOCKSCOUT["polygon"]}/api/v2/addresses/{addr}/token-balances') or []
        tot = sum(int(t.get('value') or 0) / 1e6 for t in toks
                  if ((t.get('token') or {}).get('address') or '').lower() in syms)
        return round(tot, 2)
    except Exception:
        return round(_erc20_balance('polygon', USDC_E_POLYGON, addr)
                     + _erc20_balance('polygon', USDC_POLYGON, addr), 2)

def polymarket_status():
    """Polymarket US account snapshot for the ops dashboard. Never raises."""
    out = {'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'configured': False}
    kid, sec = os.environ.get('POLYMARKET_KEY_ID', ''), os.environ.get('POLYMARKET_SECRET', '')
    if not (kid and sec):
        return out
    out['configured'] = True
    try:
        from polymarket_us import PolymarketUS
        c = PolymarketUS(key_id=kid, secret_key=sec)
        bals = ((c.account.balances() or {}).get('balances')) or []
        usd = next((b for b in bals if b.get('currency') == 'USD'), bals[0] if bals else {})
        out['balance_usd'] = round(float(usd.get('currentBalance') or 0), 2)
        out['buying_power'] = round(float(usd.get('buyingPower') or 0), 2)
        pos = ((c.portfolio.positions() or {}).get('availablePositions')) or []
        out['positions'] = [
            {'market': p.get('marketSlug') or p.get('market') or '', 'title': p.get('title') or '',
             'side': str(p.get('side') or p.get('intent') or ''), 'qty': p.get('quantity') or p.get('qty'),
             'avg': p.get('averagePrice') or p.get('avgPrice'), 'mark': p.get('markPrice') or p.get('currentPrice'),
             'pnl': p.get('unrealizedPnl') or p.get('pnl')} for p in pos][:20]
        oo = ((c.orders.list() or {}).get('orders')) or []
        out['open_orders'] = [
            {'market': o.get('marketSlug') or '', 'intent': str(o.get('intent') or ''),
             'price': (o.get('price') or {}).get('value') if isinstance(o.get('price'), dict) else o.get('price'),
             'qty': o.get('quantity') or o.get('qty')} for o in oo][:20]
    except Exception as e:
        out['error'] = str(e)[:200]
    return out

# ---------- POLYMARKET LIVE RAIL ----------
PM_LIVE = os.environ.get('POLYMARKET_LIVE', '') == '1'
PM_LIVE_CAP = float(os.environ.get('PM_LIVE_CAP', '0') or 0)  # per-bet real-$ cap (proof phase)
PM_BANKROLL_START = 50.0

def _pm_client():
    kid, sec = os.environ.get('POLYMARKET_KEY_ID', ''), os.environ.get('POLYMARKET_SECRET', '')
    if not (kid and sec):
        return None
    from polymarket_us import PolymarketUS
    return PolymarketUS(key_id=kid, secret_key=sec)

def _desk_basis(stats):
    """BASIS LAW (owner decree 2026-07-26, reaffirmed 7/29): the branded money-in is $64
    ($50 start + $14 added 7/24). Exchange-tracked net deposits only RAISE the basis when
    new money arrives — nothing ever lowers the public basis. (7/29 bug: raw net-deposits
    of $14 rendered cards as '+827% on $14 in' — fantasy math, sealed here.)"""
    return max(64.0, float((stats or {}).get('deposits') or 0))

def _desk_bankroll_txt(stats, bal):
    """ACCOUNT TRUTH LAW (owner decree 2026-07-26): every desk result / cash-out / X post shows
    the ACCOUNT — total account value and net P&L against actual funds in (deposits).
    No starting roll, no ladder framing, ever. bal['balance'] is total account value (cash+positions)."""
    dep = _desk_basis(stats)
    if bal:
        acct = round(float(bal['balance']), 2)
        net = round(acct - dep, 2)
        roi = (net / dep * 100) if dep else 0.0
        return (f"💰 account **${acct:.2f}** · net P&L **{'+' if net >= 0 else ''}${net:.2f}** "
                f"on ${dep:.2f} in ({'+' if roi >= 0 else ''}{roi:.0f}%)")
    if (stats or {}).get('account') is not None:  # SETTLE-LEDGER LAW: ledger beats "offline"
        acct = round(float(stats['account']), 2)
        net = round(acct - dep, 2)
        roi = (net / dep * 100) if dep else 0.0
        return (f"💰 account **${acct:.2f}** · net P&L **{'+' if net >= 0 else ''}${net:.2f}** "
                f"on ${dep:.2f} in ({'+' if roi >= 0 else ''}{roi:.0f}%)")
    pnl = float((stats or {}).get('pnl') or 0.0)
    return f"💰 net P&L **{'+' if pnl >= 0 else ''}${pnl:.2f}** realized on ${dep:.2f} in (live account feed offline)"

def _desk_deposits_live():
    """Funds in during the desk era, straight from the exchange ledger (completed deposits
    on/after DESK_DEPOSITS_EPOCH). None when the API won't answer — caller keeps state's number."""
    c = _pm_client()
    if not c:
        return None
    try:
        acts, cur = [], None
        for _ in range(4):
            params = {'limit': 100}
            if cur:
                params['cursor'] = cur
            r = c.portfolio.activities(params)
            batch = (r.get('activities') if isinstance(r, dict) else r) or []
            acts.extend(batch)
            if r.get('eof') or not r.get('nextCursor') or not batch:
                break
            cur = r['nextCursor']
        tot = 0.0
        for a in acts:
            if 'DEPOSIT' not in str(a.get('type')):
                continue
            chg = a.get('accountBalanceChange') or {}
            if chg.get('status') != 'ACCOUNT_BALANCE_CHANGE_STATUS_COMPLETED':
                continue
            if str(chg.get('updateTime') or '')[:10] >= DESK_DEPOSITS_EPOCH:
                tot += float((chg.get('amount') or {}).get('value') or 0)
        return round(tot, 2) if tot > 0 else None
    except Exception as e:
        print('[desk] deposits scan:', str(e)[:100])
        return None

def _desk_day_roll(stats, acct_pre):
    """PORTFOLIO-CARD LAW (owner decree 2026-07-27): anchor the account at the start of
    each ET day so every result can show 'Today +$X (+Y%)' like the app graph. The first
    sync of the day records the pre-update value as the anchor."""
    import datetime as _dt
    today = (_dt.datetime.utcnow() - _dt.timedelta(hours=4)).strftime('%Y-%m-%d')
    if stats.get('day_date') != today:
        stats['day_date'] = today
        stats['day_anchor'] = round(float(acct_pre), 2)

def _desk_portfolio_lines(stats, st):
    """The portfolio card the owner screenshotted: account balance, today's P&L %,
    deposits + net since deposit, open positions. Used on X results and desk floors."""
    dep = _desk_basis(stats)
    acct = float((stats or {}).get('account') or dep)
    anchor = float((stats or {}).get('day_anchor') or acct)
    day_pnl = acct - anchor
    day_pct = (day_pnl / anchor * 100) if anchor else 0.0
    net = acct - dep
    roi = (net / dep * 100) if dep else 0.0
    opens = [t for t in (st or {}).get('pm_trades', []) if t.get('status') == 'open']
    working = sum(float(t.get('stake') or 0) for t in opens)
    rec = f"{(stats or {}).get('wins', 0)}-{(stats or {}).get('losses', 0)}"
    l1 = f"💼 Account ${acct:.2f} · 📊 today {'+' if day_pnl >= 0 else ''}${day_pnl:.2f} ({'+' if day_pct >= 0 else ''}{day_pct:.1f}%)"
    l2 = f"📥 ${dep:.2f} deposited · net {'+' if net >= 0 else ''}${net:.2f} ({'+' if roi >= 0 else ''}{roi:.0f}%)"
    l3 = f"📂 {len(opens)} open position{'s' if len(opens) != 1 else ''} (${working:.2f} at work) · desk {rec}"
    return l1, l2, l3

def _desk_sync_money(st, stats, bal):
    """Hourly: refresh deposits from the exchange; always: persist account/net so the War Room
    and every surface show the same account truth. Returns (account, deposits, net)."""
    now = time.time()
    if now - float(st.get('dep_checked_ts') or 0) > 3600:
        live = _desk_deposits_live()
        if live:
            stats['deposits'] = live
        st['dep_checked_ts'] = now
    dep = _desk_basis(stats)
    acct = round(float(bal['balance']), 2) if bal else None
    _desk_day_roll(stats, float(stats.get('account') or acct or dep))
    net = round(acct - dep, 2) if acct is not None else None
    if acct is not None:
        stats['account'] = acct
        stats['net'] = net
    return acct, dep, net

def pm_cash_balance():
    """Real USD cash + buying power. None if unavailable. One retry — the exchange edge
    rate-limits (CF-1015) a few reads a day, and a single dead read used to kill the whole
    scan cycle. Error strings truncated: SDK exceptions carry the full CF HTML body, which
    was drowning the 100-line log window."""
    c = _pm_client()
    if not c:
        return None
    for _try in range(2):
        try:
            bals = ((c.account.balances() or {}).get('balances')) or []
            usd = next((b for b in bals if b.get('currency') == 'USD'), bals[0] if bals else {})
            return {'balance': round(float(usd.get('currentBalance') or 0), 2),
                    'buying_power': round(float(usd.get('buyingPower') or 0), 2)}
        except Exception as e:
            if _try == 0:
                time.sleep(2)
                continue
            print('[pm] balance:', str(e)[:140]); return None

def pm_find_market(team, opp, start_iso):
    """Tradable winner/moneyline market for team (vs opp) around start time. Dict or None."""
    c = _pm_client()
    if not c:
        return None
    try:
        now = time.time()
        t0, t1 = now - 14 * 3600, now + 48 * 3600
        if start_iso:
            try:
                from datetime import datetime as _dt
                t0 = _dt.fromisoformat(str(start_iso).replace('Z', '+00:00')).timestamp() - 14 * 3600
                t1 = t0 + 62 * 3600
            except Exception:
                pass
        fmt = lambda ts: time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts))
        r = c.events.list({'closed': False, 'startTimeMin': fmt(t0), 'startTimeMax': fmt(t1), 'limit': 100})
        evs = (r.get('events') if isinstance(r, dict) else r) or []
        nt, no = norm_txt(team), norm_txt(opp or '')
        for ev in evs:
            title = ev.get('title') or ev.get('name') or ''
            hay = norm_txt(title + ' ' + (ev.get('slug') or ''))
            if nt not in hay or (no and no not in hay):
                continue
            eid = ev.get('id') or ev.get('eventId')
            det = c.events.retrieve(eid) if eid else ev
            evd = (det.get('event') if isinstance(det, dict) and 'event' in det else det) or {}
            for m in evd.get('markets') or []:
                smt = str(m.get('sportsMarketType') or m.get('marketType') or '').lower()
                if 'winner' not in smt and 'moneyline' not in smt:
                    continue
                md = m.get('marketMetadata') if isinstance(m.get('marketMetadata'), dict) else {}
                oc = (md or {}).get('outcome')
                if oc and nt not in norm_txt(str(oc)):
                    continue
                sides = m.get('marketSides') or []
                long_side = next((s for s in sides if s.get('long')), sides[0] if sides else {})
                if long_side.get('tradable') is False:
                    continue
                q = long_side.get('quote') or {}
                try:
                    price = float(q.get('value') or long_side.get('price') or 0)
                except Exception:
                    price = 0
                if not (0.02 < price < 0.98):
                    continue
                return {'marketSlug': m.get('marketSlug') or m.get('slug') or '',
                        'outcome': str(oc or team), 'title': title, 'price': price,
                        'minQty': int(m.get('minOrderSize') or long_side.get('minQuantity') or 1),
                        'start': ev.get('startTime') or start_iso, 'smt': smt}
        return None
    except Exception as e:
        print('[pm] find:', str(e)[:140]); return None

def pm_place_bet(info, stake):
    """Place a capped GTC limit BUY_LONG. {'order_id','qty','price','stake'} or {'error'}."""
    if not _pm_client():
        return {'error': 'no_client'}
    try:
        bal = pm_cash_balance()
        bp = bal['buying_power'] if bal else 0.0
        stake = round(min(float(stake), bp), 2)
        if stake < 1.0:
            return {'error': 'dust'}  # clamped below $1 — cash ran out mid-batch, skip quietly
        price = float(info['price'])
        qty = int(stake / price)
        min_q = max(1, int(info.get('minQty') or 1))
        if qty < min_q:
            if min_q * price <= bp:
                qty = min_q
            else:
                return {'error': 'below_min'}
        if qty < 1:
            return {'error': 'no_liquidity'}
        stake = round(qty * price, 2)
        _intent = 'ORDER_INTENT_BUY_SHORT' if info.get('short') else 'ORDER_INTENT_BUY_LONG'
        r = _pm_client().orders.create({'marketSlug': info['marketSlug'], 'intent': _intent,
                                        'type': 'ORDER_TYPE_LIMIT',
                                        'price': {'value': f"{price:.2f}", 'currency': 'USD'},
                                        'quantity': qty, 'tif': 'TIME_IN_FORCE_GOOD_TILL_CANCEL'})
        oid = (r or {}).get('orderId') or (r or {}).get('id') or ''
        return {'order_id': oid, 'qty': qty, 'price': price, 'stake': stake}
    except Exception as e:
        return {'error': str(e)[:160]}


def pm_close_position(slug):
    """Market-exit an open position (owner decree: full control — cash out, trim, abandon)."""
    c = _pm_client()
    if not c:
        return {'error': 'no_client'}
    try:
        r = c.orders.close_position({'marketSlug': slug,
                                     'manualOrderIndicator': 'MANUAL_ORDER_INDICATOR_AUTOMATIC',
                                     'synchronousExecution': True, 'maxBlockTime': '10s',
                                     'slippageTolerance': {'toleranceBps': 300}})
        return {'ok': True, 'res': str(r)[:200]}
    except Exception as e:
        return {'error': str(e)[:160]}

def pm_check_settled(lb):
    """Position-resolution for a desk trade — graded from the EXCHANGE LEDGER, not team names.
    side = which side of the MARKET won the resolution; netPosition = OUR direction.
    A long wins iff the LONG side resolved; a short wins iff the SHORT side resolved.
    P&L = the ledger's realized value (exact cents), never a formula guess. Sport-agnostic:
    works for esports, soccer, NWSL, anything — because money doesn't need the box score."""
    c = _pm_client()
    if not c:
        return None
    try:
        r = c.portfolio.activities({'marketSlug': [lb['marketSlug']],
                                    'types': ['ACTIVITY_TYPE_POSITION_RESOLUTION']})
        acts = (r.get('activities') if isinstance(r, dict) else r) or []
        for a in acts:
            pr = a.get('positionResolution') or {}
            side = str(pr.get('side') or '')
            if not side:
                continue
            bp = pr.get('beforePosition') or {}
            ap = pr.get('afterPosition') or {}
            net = float(bp.get('netPosition') or 0)
            res_long = 'LONG' in side.upper()
            if net > 0:
                won = res_long
            elif net < 0:
                won = not res_long
            else:  # no net in record — fall back to the trade's own short flag
                won = (not res_long) if lb.get('short') else res_long
            realized = (ap.get('realized') or {}).get('value')
            if realized is not None:
                pnl = round(float(realized), 2)
                payout = round(max(float(lb.get('stake') or 0) + pnl, 0.0), 2)
            else:
                pnl = round(float(lb['qty']) - float(lb.get('stake') or 0), 2) if won else -round(float(lb.get('stake') or 0), 2)
                payout = round(float(lb['qty']) * 1.0, 2) if won else 0.0
            print(f"[desk] ledger grade: {lb['marketSlug']} side={side} net={net:g} realized={realized} -> {'WIN' if won else 'LOSS'} ${pnl:.2f}")
            return {'result': 'WIN' if won else 'LOSS', 'payout': payout, 'pnl': pnl}
        return None
    except Exception as e:
        print('[pm] settle:', str(e)[:140]); return None

def wallet_balances():
    """On-chain balances for all hot wallets + USD values. Never raises."""
    out = {'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'wallets': []}
    try:
        w = gh_get_json('wallets.json') or {}
        px = _http_json('https://api.coingecko.com/api/v3/simple/price?ids=solana,ethereum,bitcoin,binancecoin,polygon-ecosystem-token&vs_currencies=usd')
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
                    chains, tot = [], 0.0
                    for cname, (sym, px_id, _r) in EVM_RPCS.items():
                        c = {'chain': cname, 'symbol': sym}
                        try:
                            bal, rate = _evm_native(cname, addr)
                            usd_v = bal * (rate if rate else (px.get(px_id) or {}).get('usd', 0))
                            c.update(balance=round(bal, 6), usd=round(usd_v, 2))
                            tot += usd_v
                        except Exception as e:
                            c.update(balance=None, usd=None, error=str(e)[:80])
                        if cname == 'polygon':
                            try:
                                c['usdc'] = _evm_usdc_polygon(addr)
                                tot += c['usdc']
                            except Exception as e:
                                c['usdc_error'] = str(e)[:60]
                        chains.append(c)
                    entry['evm'] = chains
                    entry.update(balance=None, usd=round(tot, 2))
                    if all(c.get('error') for c in chains):
                        entry['error'] = 'all evm sources unreachable'
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
        print(f'LineShift Bot v{BOT_VERSION} online as {c.user} in {len(c.guilds)} guild(s) | privileged={privileged}')
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
        if not x_engagement_watch.is_running():
            x_engagement_watch.start()
        weekly_deepdive_watch.start()
        guarantee_watch.start()
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
        if not x_purge_old.is_running():
            x_purge_old.start()
        if not issues_sweep.is_running():
            issues_sweep.start()
        if not crypto_sync.is_running():
            crypto_sync.start()
        if not wallet_watch.is_running():
            wallet_watch.start()
        if not pm_watch.is_running():
            pm_watch.start()
        if not pm_trader.is_running():
            pm_trader.start()
        if not gw_reverify.is_running():
            gw_reverify.start()
        if not giveaway_claim_watch.is_running():
            giveaway_claim_watch.start()

    @c.event
    async def on_message(message):
        try:
            if message.author.bot:
                return
            if message.guild is None:
                # INSTANT PAYOUT LAW (owner decree 2026-07-27): a giveaway winner's DM is
                # prize business first — claim it, and if it carries a SOL address, pay NOW.
                try:
                    gd_dm = await asyncio.to_thread(gh_get_json_ref, GW_CLAIM_FILE, QUEUE_BRANCH)
                    w_dm = next((w for w in (gd_dm or {}).get('winners') or []
                                 if w.get('discord_id') == str(message.author.id)
                                 and w.get('status') in ('pending', 'claimed', 'paying')), None)
                    if w_dm is not None:
                        if w_dm.get('status') == 'pending':
                            w_dm['status'] = 'claimed'  # any DM from a winner = response, claim satisfied
                            w_dm['claimed_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                            await _gw_save_draw(gd_dm, 'claim via DM @' + w_dm.get('handle', '?'))
                        if await _gw_try_payout(message, w_dm, gd_dm):
                            return
                        if w_dm.get('status') != 'paying':
                            await message.channel.send(f"🎁 You're a **${w_dm.get('prize')} winner** — send your **Solana address** right here and the prize ships on-chain immediately. ⚡")
                        return
                except Exception as _pe:
                    print('gw payout dm:', _pe)
                # DM TICKET: private intake — relayed to the ops lab, never shown publicly.
                # This replaces open issues-room posts for anything sensitive.
                try:
                    g0 = client.guilds[0] if client.guilds else None
                    lab = find_channel(g0, 'shift-lab') if g0 else None
                    if lab:
                        await lab.send(f"🎫 **DM TICKET** — {message.author} (`{message.author.id}`):\n{(message.content or '')[:900]}")
                    await message.channel.send("🎫 Got it — that's with the team as a **private ticket**. We reply right here in this DM. ⚡")
                except Exception as e:
                    print('dm ticket:', e)
                return
            if message.guild and await shift_guard(message, message.guild):
                return
            chname = (getattr(message.channel, 'name', '') or '').lower()
            # @picks display: any member can pull their OWN room's current card + last results.
            # Read-only, tier-scoped — paid rooms never leak to each other or to general.
            try:
                low_c = (message.content or '').lower()
                if '@picks' in low_c or (c.user and c.user in message.mentions and 'pick' in low_c):
                    await show_room_picks(message)
                    return
            except Exception as e:
                print('picks display:', e)
            # @mention responder FIRST — a tag is a question to SHiFT, never an entry attempt.
            # (This used to sit below the giveaway branch, which swallowed tags in #giveaway.)
            try:
                if c.user and c.user in message.mentions:
                    is_admin = False
                    try:
                        is_admin = message.author == message.guild.owner or message.author.guild_permissions.administrator
                    except Exception:
                        pass
                    now_utc = time.time()
                    cur_h = int(time.strftime('%H', time.gmtime(now_utc)))
                    nxt = next((h for h in SCAN_SLOTS_UTC if h > cur_h), 0)
                    nxt_et = (nxt - 4) % 24
                    ampm = 'AM' if nxt_et < 12 else 'PM'
                    nxt_s = f'{nxt_et % 12 or 12} {ampm} ET'
                    if is_admin:
                        if 'giveaway' not in chname:  # QUIET-ROOM LAW v2: no mention replies in #giveaway
                            await message.channel.send(f"{message.author.mention} 🛰️ **v{BOT_VERSION}** online — next scan **{nxt_s}**. Commands route through the ops queue only — chat commands are disabled for everyone.")
                    else:
                        if 'giveaway' not in chname:
                            await message.channel.send(f"{message.author.mention} 🛰️ I'm on duty — scans drop **12a · 4a · 8a · 12p · 4p · 8p ET** (next **{nxt_s}**). Free pick in the free-pick room; paid rooms get the full board. thelineshift.github.io/SHiFTS/upgrade.html ⚡")
                    # in #giveaway with a real X handle in the same message? still process the entry below
                    if not ('giveaway' in chname and gw_handle_parse(message.content or '')):
                        return
            except Exception as e:
                print('mention responder:', e)
            if 'giveaway' in chname:
                # 24h CLAIM LAW first: a pending winner's reply claims the prize — never
                # let it fall through into entry parsing (owner decree 2026-07-27).
                try:
                    if await giveaway_claim_check_message(message):
                        return
                except Exception as _ce:
                    print('gw claim check:', _ce)
                raw = message.content or ''
                # !entry [@handle] — self-serve entry ledger (owner decree 2026-07-25).
                # A command is a question: it ALWAYS gets an answer, throttle-free.
                if raw.strip().lower().startswith('!entry'):
                    st_g = await asyncio.to_thread(get_state) or {}
                    if str(message.id) not in st_g.get('gw_handled', []):
                        await gw_mark_handled(st_g, message.id)
                        await asyncio.to_thread(gh_put, 'bot_state.json', st_g, 'gw handled')
                    await entry_status_reply(message, raw.strip()[6:].strip().lstrip('@') or None)
                    return
                # STAFF/TALK-THROUGH EXEMPTION (owner decree 7/26): not every handle typed in
                # #giveaway is an entry. Staff moderating the room never triggers entry, and a
                # message addressed TO a Discord member ("check your entry <@user>") is talk, not a ticket.
                try:
                    _p = message.author.guild_permissions
                    if message.author == message.guild.owner or _p.administrator or _p.manage_messages or _p.manage_guild:
                        return
                except Exception:
                    pass
                if message.mentions:
                    return
                st_g = await asyncio.to_thread(get_state) or {}
                if str(message.id) in st_g.get('gw_handled', []):
                    return  # already processed (edit re-fire or sweep overlap)
                hs = gw_handle_parse(raw) or gw_bare_handle(raw)
                if hs:
                    try:
                        await message.add_reaction('✅')  # IN-THE-POOL LAW (owner decree 7/26): ✅ = your ticket is IN the draw — verification level lives in the reply, never in doubt
                    except Exception:
                        pass
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
                                            'msg_id': str(message.id), 'ch_id': str(message.channel.id),
                                            'note': 'provisional - X verification unavailable; verify before draw'}
                                await asyncio.to_thread(gh_put, 'giveaway_confirmed.json', conf, 'provisional entry ' + hk, QUEUE_BRANCH)
                            # QUIET-ROOM LAW v2: provisional/repeat entries — silent ✅ only
                        except Exception as e2:
                            print('provisional fail:', e2)
                else:
                    # QUIET-ROOM LAW v2 (owner decree 2026-07-27): chatter gets NOTHING —
                    # no ack, no guide. Text in #giveaway is reserved for !entry answers,
                    # draws, winner payments, and tx links.
                    if str(message.id) not in st_g.get('gw_handled', []):
                        await gw_mark_handled(st_g, message.id)
                        await asyncio.to_thread(gh_put, 'bot_state.json', st_g, 'gw handled')
                return
            # OWNER self-serve withdrawal: shift-lab only, owner-only, two-step CONFIRM
            if 'shift-lab' in chname:
                mw = re.match(r'(?i)^withdraw\s+([\d.]+)\s*sol\s+(?:to\s+)?([1-9A-HJ-NP-Za-km-z]{32,44})\s*$', (message.content or '').strip())
                if mw:
                    try:
                        is_owner = message.author == message.guild.owner
                    except Exception:
                        is_owner = False
                    if not is_owner:
                        await message.channel.send(f"{message.author.mention} ⛔ withdrawals are owner-only.")
                        return
                    amt = float(mw.group(1))
                    dest = mw.group(2)
                    c._pending_withdraw = {'amt': amt, 'dest': dest, 'by': message.author.id, 'ts': time.time()}
                    await message.channel.send(f"💸 **WITHDRAW {amt} SOL → `{dest}`**\nReply **CONFIRM** within 60 seconds to execute. Any other message cancels.")
                    return
                if (message.content or '').strip().upper() == 'CONFIRM':
                    pend = getattr(c, '_pending_withdraw', None)
                    c._pending_withdraw = None
                    if pend and pend['by'] == message.author.id and time.time() - pend['ts'] < 60:
                        await message.channel.send(f"⏳ executing {pend['amt']} SOL → `{pend['dest']}` ...")
                        sig, err = await do_sol_transfer(pend['amt'], pend['dest'])
                        if sig:
                            await message.channel.send(f"✅ **SENT {pend['amt']} SOL → `{pend['dest']}`**\nsig: `{sig}`\nhttps://solscan.io/tx/{sig}")
                        else:
                            await message.channel.send(f"❌ withdrawal failed: {err}")
                    return
                pend2 = getattr(c, '_pending_withdraw', None)
                if isinstance(pend2, dict) and pend2.get('by') == message.author.id and not mw:
                    c._pending_withdraw = None  # any other owner message cancels a pending withdrawal
            if (message.content or '').strip().lower() == '!pin':
                _t = next((TIER_ROLES[r.name] for r in message.author.roles if r.name in TIER_ROLES), None)
                if _t:
                    try:
                        await _issue_pin_for(message.author, _t)
                        await message.reply('\U0001F4EC New PIN in your DMs — keep it private.')
                    except Exception as e:
                        await message.reply('pin machine hiccuped — try again in a minute.')
                else:
                    await message.reply('PINs are for paid members — grab a room in #\U0001F48Eupgrade first \u26a1')
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
            removed = [r for r in before.roles if r not in after.roles and r.name in TIER_ROLES]
            if removed:
                try:
                    await _revoke_pin_for(after)
                    print(f'license revoked: {after.name} lost paid role')
                except Exception as e:
                    print('pin revoke error:', e)
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
            # PIN auto-issue (v9.21.1): new paid member gets their dashboard PIN by DM
            try:
                await _issue_pin_for(after, hits[-1])
            except Exception as e:
                print('pin auto-issue error:', e)
        except Exception as e:
            print('on_member_update error:', e)
    return c

def gh_headers():
    return {'Authorization': f'token {GH_TOKEN}', 'User-Agent': 'lineshift-bot'}

def gh_get(path, ref='main'):
    req = urllib.request.Request(f'{API}/{path}?ref={ref}', headers=gh_headers())
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)

FEED_FILES = {'pins.json', 'picks.json', 'guarantee.json', 'stripe.json', 'desk.json',
              'challenge.json', 'giveaway_entries.json', 'giveaway_draw.json'}
# FEED-STORM LAW (2026-07-26 incident): every main-branch commit auto-deploys the service =
# a full bot restart = a Discord reconnect. Bot feeds (stripe.json every 30 min!) were causing
# ~60 restarts/day and pushed Discord into a Cloudflare 1015 connect ban. All bot feed I/O now
# lives on the commands branch; main carries code + site pages only. Consumers read raw either way.

def _merge_trade_lists(base_l, obj_l):
    """CLOBBER-SHIELD LAW (2026-07-26): a writer holding a minutes-stale state must NEVER resurrect
    a trade another loop already advanced — the settle loop's results kept getting clobbered back to
    'open' by the trader's stale copy (Sparta/ODDIK incident). Merge per order_id: highest status rank
    wins (settled > unfilled > resting > open — status is monotonic, a trade never un-settles);
    ties go to the writer's fresher fields."""
    rank = {'settled': 3, 'unfilled': 2, 'resting': 1, 'open': 0}
    def _key(t):
        return t.get('order_id') or f"{t.get('market_slug')}|{t.get('ts')}|{t.get('stake')}"
    by_id = {_key(t): t for t in (base_l or [])}
    for t in (obj_l or []):
        k = _key(t)
        cur = by_id.get(k)
        if cur is None or rank.get(t.get('status'), 0) >= rank.get(cur.get('status'), 0):
            by_id[k] = t
    return list(by_id.values())

def gh_put(path, obj, message, ref=QUEUE_BRANCH, _tries=3):
    """State writer with 409-retry: concurrent loops race on the same file; a sha mismatch
    just means someone else saved first — re-read, re-merge, retry instead of dying."""
    if path in FEED_FILES:
        ref = QUEUE_BRANCH  # FEED-STORM LAW: bot writes never touch main
    last = None
    for attempt in range(_tries):
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
                    for lk in ('pm_trades', 'pm2_trades', 'pm_live'):
                        if isinstance(base.get(lk), list) and isinstance(obj.get(lk), list):
                            m = _merge_trade_lists(base[lk], obj[lk])  # CLOBBER-SHIELD LAW
                            base[lk] = m
                            obj = {**obj, lk: m}
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
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                _resp = json.load(r)
                if path == 'bot_state.json':
                    _STATE_CACHE.update(data=obj, ts=time.time())  # write-through (USAGE LAW)
                return _resp
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 409 and attempt < _tries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
    if last:
        raise last
def fetch_commands():
    try:
        req = urllib.request.Request(f'{RAW}/bot_commands.json?t={int(time.time())}',
                                     headers={'User-Agent': 'lineshift-bot'})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except Exception:
        return None

_STATE_CACHE = {'data': None, 'ts': 0.0}
STATE_CACHE_TTL = 30  # USAGE LAW: one in-memory state copy shared by all loops; writes update it
# instantly (write-through). Cuts thousands of identical 214KB GitHub reads/day; loops still read
# fresh-enough state, and the CLOBBER-SHIELD in gh_put guards every write regardless.

def get_state(force=False):
    """Shared-cache read (USAGE LAW): fresh fetch at most every STATE_CACHE_TTL s; on a GitHub blip
    serve the last copy instead of None — loops keep working, no retry storms."""
    now = time.time()
    if not force and _STATE_CACHE['data'] is not None and now - _STATE_CACHE['ts'] < STATE_CACHE_TTL:
        return _STATE_CACHE['data']
    try:
        d = gh_get('bot_state.json', ref=QUEUE_BRANCH)
        st = json.loads(base64.b64decode(d['content']))
        _STATE_CACHE.update(data=st, ts=now)
        return st
    except Exception:
        return _STATE_CACHE['data']

def gh_get_json(path):
    try:
        d = gh_get(path, ref=QUEUE_BRANCH)
        return json.loads(base64.b64decode(d['content']))
    except Exception:
        return {}

def gh_get_json_ref(path, ref):
    if path in FEED_FILES:
        ref = QUEUE_BRANCH  # FEED-STORM LAW
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
    'weekly-analytics': ['sharp-analytics'], 'weekly-deepdive': ['whale-deepdive', 'monthly-deepdive'], 'monthly-deepdive': ['whale-deepdive'],
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
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="media_category"\r\n\r\ntweet_image\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="media"; filename="card.png"\r\n'
            f'Content-Type: {mime}\r\n\r\n').encode() + img_bytes + f'\r\n--{boundary}--\r\n'.encode()
    req = urllib.request.Request(url, data=body, method='POST',
                                 headers={'Authorization': f"Bearer {c['oauth2_access']}",
                                          'Content-Type': f'multipart/form-data; boundary={boundary}'})
    with urllib.request.urlopen(req, timeout=40) as r:
        d = json.load(r)
    return str(d.get('data', {}).get('id') or d.get('media_id_string') or d.get('media_id'))

X_BIO_V = 2  # bump to re-push the profile bio (owner decree 2026-07-26: advertise league coverage)
X_BIO_TEXT = "⚡ SHiFT's Picks — receipted picks + a live Polymarket desk. Watching NFL NBA MLB NHL UFC NCAA soccer + CS2 LoL Dota2 Valorant esports 💎"

def x_update_bio(text, url_field='https://thelineshift.github.io/SHiFTS/upgrade.html'):
    """One-shot profile update (v1.1 form post — body params must join the OAuth1 base string)."""
    import hmac as _h, hashlib as _hl, secrets as _sc, urllib.parse as _up
    from urllib.parse import quote as _qq
    c = x_creds_load()
    url = 'https://api.x.com/1.1/account/update_profile.json'
    params = {'description': text, 'url': url_field}
    op = {'oauth_consumer_key': c['api_key'], 'oauth_nonce': _sc.token_hex(16),
          'oauth_signature_method': 'HMAC-SHA1', 'oauth_timestamp': str(int(time.time())),
          'oauth_token': c['access_token'], 'oauth_version': '1.0'}
    q = lambda s: _qq(str(s), safe='')
    allp = {**params, **op}
    base = '&'.join(['POST', q(url), q('&'.join(f'{q(k)}={q(v)}' for k, v in sorted(allp.items())))])
    key = f"{q(c['api_secret'])}&{q(c['access_token_secret'])}"
    op['oauth_signature'] = base64.b64encode(_h.new(key.encode(), base.encode(), _hl.sha1).digest()).decode()
    auth = 'OAuth ' + ', '.join(f'{k}="{q(v)}"' for k, v in sorted(op.items()))
    req = urllib.request.Request(url, data=_up.urlencode(params).encode(), method='POST',
                                 headers={'Authorization': auth, 'Content-Type': 'application/x-www-form-urlencoded'})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)

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
    lines = [line(followed, 'Follow @SHiFTSPicks'),
             line(liked, 'Like the giveaway post'),
             line(reposted, 'Repost the giveaway post')]
    missing = [l for ok, l in zip((followed, liked, reposted),
                                  ('follow @SHiFTSPicks', 'like the giveaway post', 'repost the giveaway post')) if not ok]
    return lines, missing

GW_BAD = ('thelineshift', 'shiftspicks', 'everyone', 'here', 'status', 'home', 'search', 'explore', 'i', 'yourhandle')
GW_POST_DEFAULT = '2080027230839931367'

def gw_post_link(st):
    pid = (st or {}).get('giveaway_x_post', GW_POST_DEFAULT)
    return f'https://x.com/SHiFTSPicks/status/{pid}'

GW_STEPS = "Entry needs 3 steps on X: ✅ Follow @SHiFTSPicks · ❤️ Like the giveaway post · 🔁 Repost it — this exact one: {link}"

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

GW_WORDS = ('handle', 'twitter', 'giveaway', 'enter', 'entry', 'done', 'following', 'reposted', 'liked',
            'thanks', 'thank', 'hello', 'admin', 'shift', 'please', 'when', 'draw', 'winner', 'winners',
            'good', 'luck', 'yeah', 'nice', 'cool', 'love', 'this', 'that', 'what', 'where', 'how', 'yes')

def gw_bare_handle(raw):
    """BARE-HANDLE FALLBACK (never-silent law): a lone token in #giveaway is almost always an X handle."""
    s = (raw or '').strip().strip('@').strip()
    if not re.fullmatch(r'[A-Za-z0-9_]{4,15}', s):
        return []
    if s.lower() in GW_BAD or s.lower() in GW_WORDS:
        return []
    return [s]

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

def _gw_post_ids(state):
    """All giveaway posts whose engagement counts: canonical first, superseded ones kept
    so nobody loses steps when the promo is reposted (owner decree 2026-07-25)."""
    posts = list((state or {}).get('giveaway_x_posts') or [])
    cur = (state or {}).get('giveaway_x_post', '')
    if cur and cur not in posts:
        posts.insert(0, cur)
    return [p for p in posts if p]


async def _gw_live_checks(handle, state):
    """Guarded X step check. Returns (followed, liked, reposted) — None where X won't say."""
    followed = liked = reposted = None
    try:
        bt = (await asyncio.to_thread(x_creds_load))['bearer_token']
        u = await asyncio.to_thread(x_get_json, f'https://api.x.com/2/users/by/username/{handle}', bt)
        uid = str(u.get('data', {}).get('id') or '')
        if not uid:
            return False, liked, reposted
        followed = await asyncio.to_thread(gw_followed, uid, bt)
        posts = _gw_post_ids(state)
        if posts:
            # QUOTA LAW (7/26): free-tier X reads are starved — spend calls only on what GATES entry.
            # Repost is advisory under REPOST-INFERENCE (taken their word), so retweeted_by
            # is never fetched anymore. Halves the burn per entry.
            lsets = []
            for pid in posts:
                try:
                    lsets.append(await asyncio.to_thread(gw_user_set, f'https://api.x.com/2/tweets/{pid}/liking_users?max_results=100', bt))
                except Exception:
                    lsets.append(None)
            okl = [s for s in lsets if s is not None]
            liked = any(uid in s for s in okl) if okl else None
            reposted = None  # advisory only — see REPOST-INFERENCE LAW
    except Exception:
        pass
    return followed, liked, reposted

def _gw_mult(author):
    names = [r.name for r in getattr(author, 'roles', [])]
    tkey = 'whale' if any('Whale' in n or '🐋' in n for n in names) else 'sharp' if any('Sharp' in n or '📊' in n for n in names) else 'lock' if any('Lock' in n or '🔒' in n for n in names) else 'free'
    return {'whale': 5, 'sharp': 3, 'lock': 2, 'free': 1}[tkey]

async def entry_status_reply(message, handle_arg=None):
    """!entry — the entry ledger on demand: handle on file, every step, tickets, draw time."""
    try:
        conf = await asyncio.to_thread(gh_get_json_ref, 'giveaway_confirmed.json', QUEUE_BRANCH) or {}
        rec, hk = None, None
        if handle_arg:
            hk = handle_arg.lower()
            rec = conf.get(hk)
        else:
            for k, v in conf.items():
                if str(v.get('discord_id')) == str(message.author.id):
                    rec, hk = v, k
                    break
        state = await asyncio.to_thread(get_state)
        link = gw_post_link(state)
        if not rec:
            who = f"@{handle_arg}" if handle_arg else "you"
            await message.channel.send(
                f"{message.author.mention} 🔎 **ENTRY CHECK** — no entry on file for {who}.\n"
                f"Drop your **X handle** in this room (like `@yourhandle`), then complete the steps on {link} — I'll log you in seconds. ⚡")
            return
        handle = rec.get('handle') or hk
        followed, liked, reposted = await _gw_live_checks(handle, state)
        prov = 'provisional' in str(rec.get('note', '')).lower()
        # live check just cleared everything? upgrade on the spot
        if prov and followed and liked and reposted:
            rec.pop('note', None)
            rec['ts_upgraded'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            conf[hk] = rec
            await asyncio.to_thread(gh_put, 'giveaway_confirmed.json', conf, 'giveaway upgrade ' + hk, QUEUE_BRANCH)
            await _gw_mark_entered(message.guild, rec)
            prov = False
            status = '🔒 **CONFIRMED** — upgraded you right now'
        elif prov:
            status = '⏳ **PROVISIONAL** — in the pool, verification pending'
        else:
            status = '🔒 **CONFIRMED** — in the pool'
        def ic(ok, label):
            return f"{'✅' if ok else ('❌' if ok is False else '❓')} {label}"
        checklist = "\n".join([ic(followed, 'Follow @SHiFTSPicks'), ic(liked, 'Like the giveaway post'), ic(reposted, 'Repost the giveaway post')])
        todo = []
        if followed is False: todo.append('follow @SHiFTSPicks')
        if liked is False: todo.append('like the post')
        if reposted is False: todo.append('repost the post')
        todo_s = f"\n**Still to do:** {' + '.join(todo)} — on {link}" if todo else ''
        xdeg = "\n_X checks are degraded right now — ❓ steps get re-scanned automatically before the draw._" if (followed is None or liked is None or reposted is None) else ''
        await message.channel.send(
            f"{message.author.mention} 🎫 **ENTRY STATUS — @{handle}**\n{status}\n\n{checklist}\n"
            f"🎟️ Tickets: **{rec.get('mult', 1)}x** · entered {str(rec.get('ts', ''))[:10]}{todo_s}{xdeg}\n"
            f"Draw: **Sunday 6 PM ET** — provably fair, paid on-chain. ⚡")
    except Exception as e:
        print('entry status:', e)
        try:
            await message.channel.send(f"{message.author.mention} 🎫 entry ledger is being stubborn — try again in a minute. ⚡")
        except Exception:
            pass

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
            await gw_reply_once(message, 'nohandle', f"⚡ entry check: I can't find an X account **@{handle}** — double-check the spelling and drop it again.", hours=4)
            return
        # bearer (app-only) covers all public reads — no user token needed (7/24 fix)
        followed = await asyncio.to_thread(gw_followed, uid, bt)
        try:
            _f, liked, reposted = await _gw_live_checks(handle, state)
            followed = followed if followed is not None else _f
        except Exception:
            liked = reposted = None
        def ic(ok, label):
            return f"{'✅' if ok else ('❌' if ok is False else '❓')} {label}"
        checklist = "\n".join([ic(followed, 'Follow @SHiFTSPicks'), ic(liked, 'Like the giveaway post'), ic(reposted, 'Repost the giveaway post')])
        # REPOST-INFERENCE LAW (owner decree 7/26): like + follow verified = full entry —
        # "if they liked and followed they probably reposted." Repost stays on the
        # checklist as advisory only, never a gate.
        missing = []
        if followed is False: missing.append('follow @SHiFTSPicks')
        if liked is False: missing.append('like the giveaway post')
        full_ok = bool(followed) and bool(liked)
        if reposted is not True:
            checklist = checklist.replace(ic(reposted, 'Repost the giveaway post'),
                                          '✅ Repost the giveaway post — taking your word for it')
        if full_ok:
            conf = await asyncio.to_thread(gh_get_json_ref, 'giveaway_confirmed.json', QUEUE_BRANCH)
            _ex = (conf or {}).get(handle.lower())
            if _ex and 'provisional' not in str(_ex.get('note', '')).lower():
                return  # QUIET-ROOM LAW v2: already in the pool — silence, no re-reply
            names = [r.name for r in getattr(message.author, 'roles', [])]
            tkey = 'whale' if any('Whale' in n or '🐋' in n for n in names) else 'sharp' if any('Sharp' in n or '📊' in n for n in names) else 'lock' if any('Lock' in n or '🔒' in n for n in names) else 'free'
            mult = {'whale': 5, 'sharp': 3, 'lock': 2, 'free': 1}[tkey]
            conf = await asyncio.to_thread(gh_get_json_ref, 'giveaway_confirmed.json', QUEUE_BRANCH)
            conf = conf or {}
            conf[handle.lower()] = {
                'handle': handle, 'discord': str(message.author), 'discord_id': str(message.author.id),
                'mult': mult, 'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'msg_id': str(message.id), 'ch_id': str(message.channel.id)}
            await asyncio.to_thread(gh_put, 'giveaway_confirmed.json', conf, 'giveaway confirm ' + handle, QUEUE_BRANCH)
            try:
                await message.add_reaction('✅')
                try:
                    await message.remove_reaction('⏳', client.user)  # REACTION LAW: full entry replaces pending
                except Exception:
                    pass
            except Exception:
                pass
            # QUIET-ROOM LAW v2 (owner decree 2026-07-27): no text reply — the ✅ reaction
            # IS the confirmation. Channel text is reserved for !entry answers, draws,
            # winner payments, and tx links.
        else:
            # PROVISIONAL-BY-DEFAULT LAW (owner decree 2026-07-25): an incomplete or
            # unverifiable entry is STILL logged instantly — nobody waits silent on X.
            conf = await asyncio.to_thread(gh_get_json_ref, 'giveaway_confirmed.json', QUEUE_BRANCH) or {}
            hk = handle.lower()
            if hk not in conf:
                mult = _gw_mult(message.author)
                why = '+'.join(missing) if missing else 'x-degraded'
                conf[hk] = {'handle': handle, 'discord': str(message.author), 'discord_id': str(message.author.id),
                            'mult': mult, 'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                            'msg_id': str(message.id), 'ch_id': str(message.channel.id),
                            'note': f'provisional — {why}'}
                await asyncio.to_thread(gh_put, 'giveaway_confirmed.json', conf, 'provisional entry ' + hk, QUEUE_BRANCH)
            # QUIET-ROOM LAW v2: provisional entries and step-checks are silent — ✅ = in
            # the pool, upgrades happen in the background, details live behind !entry.
    except Exception as e:
        print('giveaway verify error:', e)


async def _gw_mark_entered(g0, rec):
    """REACTION LAW: when an entry goes full, swap ⏳ -> ✅ on the original entry message."""
    try:
        ch_id, msg_id = rec.get('ch_id'), rec.get('msg_id')
        if not (g0 and ch_id and msg_id):
            return
        ch = g0.get_channel(int(ch_id))
        if not ch:
            return
        msg = await ch.fetch_message(int(msg_id))
        try:
            await msg.remove_reaction('⏳', client.user)
        except Exception:
            pass
        await msg.add_reaction('✅')
    except Exception as e:
        print('gw mark entered:', e)


@tasks.loop(seconds=7200)
async def gw_reverify():
    """Every 2h: re-scan provisional entries — the moment X cooperates, they upgrade + get told."""
    try:
        conf = await asyncio.to_thread(gh_get_json_ref, 'giveaway_confirmed.json', QUEUE_BRANCH) or {}
        provs = {k: v for k, v in conf.items() if 'provisional' in str(v.get('note', '')).lower()}
        if not provs:
            return
        state = await asyncio.to_thread(get_state)
        g0 = client.guilds[0] if client.guilds else None
        gch = find_channel(g0, 'giveaway') if g0 else None
        changed = False
        for hk, rec in provs.items():
            try:
                followed, liked, reposted = await _gw_live_checks(rec.get('handle') or hk, state)
            except Exception:
                continue
            if followed and liked:  # REPOST-INFERENCE LAW (owner decree 7/26): follow + like = full
                rec = dict(rec)
                rec.pop('note', None)
                rec['ts_upgraded'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                conf[hk] = rec
                changed = True
                # QUIET-ROOM LAW v2: reverify upgrades are silent — the ⏳→✅ swap on the
                # member's post is the whole announcement.
                await _gw_mark_entered(g0, rec)
            await asyncio.sleep(2)
        if changed:
            await asyncio.to_thread(gh_put, 'giveaway_confirmed.json', conf, 'giveaway reverify upgrades', QUEUE_BRANCH)
    except Exception as e:
        print('gw reverify:', e)


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
                if sev.get(key) not in ('ok', 'ok-bot') and (delta_h > 0 or mins_past > 40):
                    # CARD LAW: a missed slot is never excused with a note — the card must
                    # still drop. Run the most recent missed slot LIVE right now; the full
                    # card posts to every room and the slot gets marked, all inside the run.
                    try:
                        await scan_engine_run(g0, key, False)
                        report.append(f'🛰️ recovery scan {key} fired — card posted to all rooms')
                    except Exception as _re:
                        report.append(f'⚠️ recovery scan {key} failed: {_re}')
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
                if m.author.bot:
                    continue
                # STAFF/TALK-THROUGH EXEMPTION (owner decree 7/26) — same law as live intake:
                # staff messages and messages addressed to a member are not entries.
                try:
                    _p = m.author.guild_permissions
                    if m.author == g0.owner or _p.administrator or _p.manage_messages or _p.manage_guild:
                        continue
                except Exception:
                    pass
                if m.mentions:
                    continue
                raw = m.content or ''
                hs = gw_handle_parse(raw) or gw_bare_handle(raw)
                if not hs:
                    continue  # guidance for no-handle posts is on_message's job; sweep never nags
                if hs[0].lower() in conf:
                    if str(m.id) not in handled:
                        await gw_mark_handled(st_g, m.id)
                        dirty = True
                    continue  # on the ledger — sweeps never nag
                # BACKFILL LAW (2026-07-25): a parsed handle that's NOT on the ledger gets
                # processed even if its message was "handled" before the provisional law —
                # those are the people who heard nothing back.
                if str(m.id) not in handled:
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
                            await gch.send(f"{m.author.mention} 🎫 **YOU'RE IN THE POOL — @{hs[0]}** — caught up after a restart; your ticket is in Sunday's draw, verification finishes automatically. ⚡")
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

def gh_get_json_main(path):
    try:
        d = gh_get(path, ref=(QUEUE_BRANCH if path in FEED_FILES else 'main'))  # FEED-STORM LAW
        return json.loads(base64.b64decode(d['content']))
    except Exception:
        return {}

def _new_license():
    """Unbrute-forceable member key: SHFT-XXXX-XXXX over a 32-char unambiguous alphabet (~1.1e12 space)."""
    import secrets
    A = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    return 'SHFT-' + ''.join(secrets.choice(A) for _ in range(4)) + '-' + ''.join(secrets.choice(A) for _ in range(4))

async def _issue_pin_for(member, tier):
    """Issue a War Room license key: revoke any old key of theirs, store only the new hash, DM the key."""
    import hashlib
    key = _new_license()
    h = hashlib.sha256(key.encode()).hexdigest()
    pins = await asyncio.to_thread(gh_get_json_main, 'pins.json')
    store = pins.setdefault('pins', {})
    did = str(member.id)
    for old_h in [k for k, v in store.items() if str(v.get('did')) == did]:
        store.pop(old_h, None)
    store[h] = {'tier': tier, 'did': did, 'since': time.strftime('%Y-%m-%d', time.gmtime())}
    pins['updated'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    await asyncio.to_thread(gh_put, 'pins.json', pins, f'license issued ({tier})', 'main')
    try:
        await member.send(f"\U0001F511 **Your SHiFT's Picks War Room key:** `{key}`\nUnlock your **{tier.title()}** view: https://thelineshift.github.io/SHiFTS/dashboard.html\n\u26a0\uFE0F One key per member, tied to your Discord — shared keys get revoked, and every page you view carries your traceable member mark. Type `!pin` anytime to rotate.")
    except Exception:
        pass

async def _revoke_pin_for(member):
    """Kill a member's War Room access the moment their paid role drops."""
    pins = await asyncio.to_thread(gh_get_json_main, 'pins.json')
    store = pins.get('pins') or {}
    did = str(member.id)
    doomed = [k for k, v in store.items() if str(v.get('did')) == did]
    if doomed:
        for k in doomed:
            store.pop(k, None)
        pins['updated'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        await asyncio.to_thread(gh_put, 'pins.json', pins, 'license revoked (role loss)', 'main')

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
    elif a == 'lock_channel':
        try:
            chan = find_channel(guild, cmd.get('channel', ''))
            if not chan:
                log.append(f"lock_channel FAIL: no such channel {cmd.get('channel')}")
            else:
                everyone = guild.default_role
                await chan.set_permissions(everyone, overwrite=discord.PermissionOverwrite(view_channel=False))
                for rn in cmd.get('roles', ('lock', 'sharp', 'whale')):
                    role = find_role(guild, rn)
                    if role:
                        await chan.set_permissions(role, overwrite=discord.PermissionOverwrite(view_channel=True, read_message_history=True))
                await chan.set_permissions(guild.me, overwrite=discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, embed_links=True, attach_files=True))
                log.append(f'lock_channel OK: #{chan.name} locked to paid tiers (lock/sharp/whale)')
        except Exception as e:
            log.append(f'lock_channel FAIL: {e}')
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
                    checklist = "\n".join([ic(followed, 'Follow @SHiFTSPicks'), ic(liked, 'Like the giveaway post'), ic(reposted, 'Repost the giveaway post')])
                    missing = []
                    if followed is False: missing.append('follow @SHiFTSPicks')
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
    elif a == 'issue_pins':
        sent = 0
        for m in guild.members:
            if m.bot:
                continue
            tier = next((TIER_ROLES[r.name] for r in m.roles if r.name in TIER_ROLES), None)
            if not tier:
                continue
            try:
                await _issue_pin_for(m, tier)
                sent += 1
            except Exception:
                pass
        log.append(f'issue_pins: {sent} license keys issued (revoke+replace)')
    elif a == 'room_tail':
        ch = find_channel(guild, cmd['channel'])
        if not ch:
            log.append(f"room_tail: channel '{cmd['channel']}' not found")
        else:
            n = int(cmd.get('n', 3))
            msgs = [m async for m in ch.history(limit=n)]
            if not msgs:
                log.append(f'#{ch.name}: empty')
            for m in msgs:
                txt = (m.content or '') or ((m.embeds[0].description or '') if m.embeds else '')
                log.append(f'#{ch.name} [{m.created_at.strftime("%H:%M")}] {txt[:110]}')
    elif a == 'x_bio':
        try:
            res = await asyncio.to_thread(x_update_bio, cmd['text'], cmd.get('url'))
            if isinstance(res, dict) and res.get('error'):
                log.append(f"x_bio FAIL: {res['error']}")
            else:
                log.append('x_bio OK')
        except Exception as e:
            log.append(f'x_bio FAIL: {e}')
    elif a == 'x_post_text':
        try:
            res = await asyncio.to_thread(x_post, cmd['text'], cmd.get('quote_id'))
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
            for host, path, meth in [('api.x.com', f'/2/users/{uid}/pinned', 'POST'),
                                     ('api.twitter.com', f'/2/users/{uid}/pinned', 'POST'),
                                     ('api.x.com', f'/2/users/{uid}/pinned_tweets', 'PUT')]:
                try:
                    req = urllib.request.Request(f'https://{host}{path}',
                        data=payload, headers={'Authorization': f"Bearer {c['oauth2_access']}",
                                               'Content-Type': 'application/json'}, method=meth)
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
                     'order_id': f'{member.id}:{tier}', 'order_description': f"SHiFT's Picks {tier.title()} 30 days",
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
    elif a == 'x_pin':
        try:
            tw = str(cmd.get('tweet_id'))
            uid2 = '1831457082828021760'
            url = f'https://api.x.com/2/users/{uid2}/pinned_tweets'
            ok, last_e = False, None
            for name, ck, cs, at, ats in x_oauth1_sets(x_creds_load()):
                try:
                    hdr = x_oauth1_sign('PUT', url, ck, cs, at, ats)
                    req = urllib.request.Request(url, data=json.dumps({'tweet_id': tw}).encode(), method='PUT',
                        headers={'Authorization': hdr, 'Content-Type': 'application/json', 'User-Agent': 'SHiFTPicks/1.0'})
                    with urllib.request.urlopen(req, timeout=25) as r:
                        resp = json.load(r)
                    ok = True
                    log.append(f'x_pin OK: {tw} pinned via {name} -> {resp}')
                    break
                except Exception as e:
                    last_e = f'{name}: {e}'
            if not ok:
                log.append(f'x_pin FAIL: {last_e}')
        except Exception as e:
            log.append(f'x_pin FAIL: {e}')
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
                # OWNER LAW: never post "link online" system noise to X — link status goes to shift-lab only.
                try:
                    lab = find_channel(guild, 'shift-lab')
                    if lab:
                        await lab.send(f"🔗 X user link refreshed ({time.strftime('%H:%M UTC')}) — posts/deletes live. (No X announcement, per standing order.)")
                except Exception as he:
                    print('link lab note:', he)
            except urllib.error.HTTPError as e:
                log.append(f'x_link_finish FAIL: HTTP {e.code}: {e.read()[:250]}')
            except Exception as e:
                log.append(f'x_link_finish FAIL: {e}')
    elif a == 'x_diag':
        c = x_creds_load()
        # (a) bearer app-only read
        try:
            req = urllib.request.Request('https://api.x.com/2/users/by/username/SHiFTSPicks',
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
            res = await asyncio.to_thread(x_post_native, cmd.get('text', '\u26a1 The board never sleeps. New card every 4 hours.'))
            log.append(f'x_test OK: tweet id {res.get("data", {}).get("id") if res else None}')
        except Exception as e:
            log.append(f'x_test FAIL: {e}')
    elif a == 'dm_reply':
        try:
            u = await client.fetch_user(int(cmd['user_id']))
            await u.send(cmd.get('text', '')[:1900])
            log.append(f'dm_reply OK -> {cmd["user_id"]}')
        except Exception as e:
            log.append(f'dm_reply FAIL: {e}')
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
    elif a == 'pm_close':
        tgt = cmd.get('slug') or ''
        if tgt == 'all':
            slugs = sorted({t['market_slug'] for t in st.get('pm_trades', []) if t.get('status') == 'open'})
        else:
            slugs = [tgt]
        for s2 in slugs:
            res = await asyncio.to_thread(pm_close_position, s2)
            log.append(f"pm_close {s2}: {res.get('error') or str(res.get('res'))[:140]}")
            for t in st.get('pm_trades', []):
                if t.get('market_slug') == s2 and t.get('status') == 'open':
                    t['status'] = 'closed-manual'
                    t['closed_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
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
        # optional slot override (e.g. '20260724-20') — '-manual' suffix keeps the REAL
        # slot's done-marker and registration space untouched
        slot_key = (cmd.get('slot') or time.strftime('%Y%m%d-%H', time.gmtime())) + '-manual'
        if not cmd.get('force'):
            st_guard = await asyncio.to_thread(get_state)
            if (st_guard.get('scan_events') or {}).get(slot_key) == 'ok-bot':
                log.append(f'scan {slot_key} already dealt — pass force:true to rebuild')
                return
        _SCAN_DONE.add(slot_key)
        await scan_engine_run(guild, slot_key, dry_run)
        log.append(f'scan_now executed (dry={dry_run}, slot={slot_key})')
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
            try:
                _p = m.author.guild_permissions
                if m.author == guild.owner or _p.administrator or _p.manage_messages or _p.manage_guild:
                    continue  # STAFF/TALK-THROUGH EXEMPTION (owner decree 7/26)
            except Exception:
                pass
            if m.mentions:
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
        sig, err = await do_sol_transfer(sol, to)
        if sig:
            msg = f'💸 WITHDRAW SOL — {sol} SOL -> {to} | sig: {sig} | solscan.io/tx/{sig}'
            lab = find_channel(guild, 'shift-lab')
            if lab:
                await lab.send(msg)
            log.append(msg)
        else:
            log.append(f'withdraw_sol FAIL: {err}')
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
        checklist = "\n".join([ic(followed, 'Follow @SHiFTSPicks'), ic(liked, 'Like the giveaway post'), ic(reposted, 'Repost the giveaway post')])
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
            if followed is False: missing.append('follow @SHiFTSPicks')
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
        wh = await ch.create_webhook(name=cmd.get('name', 'SHiFT'))
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

# ============================ WEEKLY DEEP-DIVE ENGINE (v9.21.0) ============================
# Whale: full autopsy of the week's Whale plays + desk floor — why each winner was chosen,
# every factor in SHiFT's read, charts & stats. Sharp: lighter weekly breakdown. Saturdays 4 PM ET.
def _dd_week_picks(tier, days=7):
    try:
        _d = gh_get('picks.json')
        pk = json.loads(base64.b64decode(_d['content'])) if _d and 'content' in _d else {}
        out = []
        cutoff = time.time() - days * 86400
        for p in pk.get('picks', []):
            if p.get('tier') != tier or 'result' not in p:
                continue
            try:
                ts = datetime.datetime.fromisoformat(str(p.get('date', '')).replace('Z', '+00:00')).timestamp()
            except Exception:
                continue
            if ts >= cutoff:
                out.append(p)
        return out
    except Exception as e:
        print('deepdive picks fetch:', e)
        return []

def _dd_stats(ps):
    w = sum(1 for p in ps if str(p.get('result')).lower() in ('won','win','✅'))
    l = sum(1 for p in ps if str(p.get('result')).lower() in ('lost','loss','❌'))
    pu = sum(1 for p in ps if str(p.get('result')).lower() in ('push','push 🔄'))
    units = 0.0
    for p in ps:
        try: units += float(p.get('units_result') if p.get('units_result') is not None else p.get('profit') or 0)
        except Exception: pass
    staked = sum(float(p.get('units') or 1) for p in ps) or 1.0
    by = {}
    for p in ps:
        s = str(p.get('sport') or '?').upper()
        r = str(p.get('result')).lower()
        b = by.setdefault(s, [0,0])
        if r in ('won','win','✅'): b[0] += 1
        elif r in ('lost','loss','❌'): b[1] += 1
    return {'w': w, 'l': l, 'push': pu, 'units': round(units, 2), 'roi': round(100.0 * units / staked, 1), 'by_sport': by}

def _dd_cum(ps):
    pts, run = [], 0.0
    for p in sorted(ps, key=lambda x: str(x.get('date',''))):
        try: run += float(p.get('units_result') if p.get('units_result') is not None else p.get('profit') or 0)
        except Exception: pass
        pts.append(round(run, 2))
    return pts

def _dd_font(sz, bold=False):
    from PIL import ImageFont
    try:
        return ImageFont.truetype('DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf', sz)
    except Exception:
        return ImageFont.load_default()

def _dd_chrome(d, W, H, title, sub):
    """Brand header + footer shared by all deep-dive renders."""
    d.rectangle((0, 0, W, 74), fill=(13, 26, 43))
    d.rectangle((0, 74, W, 76), fill=(45, 212, 191))
    d.text((28, 16), "⚡ SHiFT'S PICKS", fill=(45, 212, 191), font=_dd_font(26, True))
    d.text((28, 46), title, fill=(232, 241, 250), font=_dd_font(19, True))
    tw = d.textlength(sub, font=_dd_font(15))
    d.text((W - tw - 28, 30), sub, fill=(139, 167, 196), font=_dd_font(15))
    d.text((28, H - 30), "every play receipted on the public ledger · @SHiFTSPicks", fill=(70, 95, 125), font=_dd_font(13))

def _dd_chart_pnl(pts, path, title):
    """P&L curve — v9.23.0 premium: gridlines + axis labels + area fill + end badge."""
    from PIL import Image, ImageDraw
    W, H = 1000, 560
    BG, LINE, TXT, DIM = (10, 20, 32), (30, 58, 92), (232, 241, 250), (139, 167, 196)
    TEAL, RED, GOLD = (45, 212, 191), (245, 101, 101), (245, 197, 24)
    img = Image.new('RGB', (W, H), BG); d = ImageDraw.Draw(img)
    _dd_chrome(d, W, H, title, "WEEKLY WHALE DEEP-DIVE")
    if not pts: pts = [0.0]
    lo, hi = min(pts + [0]), max(pts + [0])
    rng = (hi - lo) or 1.0
    pad = rng * 0.12
    lo, hi = lo - pad, hi + pad
    rng = hi - lo
    x0, x1, y0, y1 = 84, W - 48, 116, H - 74
    # gridlines + labels (5 rows)
    for gi in range(6):
        vv = lo + rng * gi / 5
        yy = y1 - ((vv - lo) / rng) * (y1 - y0)
        d.line((x0, yy, x1, yy), fill=(22, 40, 64), width=1)
        d.text((18, yy - 9), f"{vv:+.1f}", fill=DIM, font=_dd_font(13))
    zy = y1 - ((0 - lo) / rng) * (y1 - y0)
    d.line((x0, zy, x1, zy), fill=(60, 90, 130), width=2)
    d.text((x0 + 4, zy - 18), "breakeven", fill=(90, 120, 155), font=_dd_font(12))
    def px(i): return x0 + (i / max(1, len(pts) - 1)) * (x1 - x0)
    def py(val): return y1 - ((val - lo) / rng) * (y1 - y0)
    col = TEAL if pts[-1] >= 0 else RED
    fillc = (18, 52, 50) if pts[-1] >= 0 else (58, 26, 32)
    # area fill
    poly = [(px(0), zy)] + [(px(i), py(vv)) for i, vv in enumerate(pts)] + [(px(len(pts) - 1), zy)]
    d.polygon(poly, fill=fillc)
    for i in range(1, len(pts)):
        d.line((px(i-1), py(pts[i-1]), px(i), py(pts[i])), fill=col, width=4)
    for i, vv in enumerate(pts):
        d.ellipse((px(i)-4, py(vv)-4, px(i)+4, py(vv)+4), fill=col, outline=BG, width=2)
    # peak / trough callouts
    if len(pts) > 2:
        imax, imin = pts.index(max(pts)), pts.index(min(pts))
        d.text((px(imax) - 18, py(pts[imax]) - 26), f"+{pts[imax]:.2f}u" if pts[imax] >= 0 else f"{pts[imax]:.2f}u", fill=TEAL, font=_dd_font(13, True))
        if imin != len(pts) - 1:
            d.text((px(imin) - 18, py(pts[imin]) + 10), f"{pts[imin]:.2f}u", fill=RED, font=_dd_font(13, True))
    # end badge
    end = pts[-1]
    badge = f"{'+' if end >= 0 else ''}{end:.2f}u this week"
    bw = d.textlength(badge, font=_dd_font(20, True)) + 28
    bx = min(max(px(len(pts) - 1) - bw - 12, x0), x1 - bw)
    by_ = max(py(end) - 46, y0 - 8)
    d.rounded_rectangle((bx, by_, bx + bw, by_ + 34), radius=9, fill=(18, 38, 60), outline=col, width=2)
    d.text((bx + 14, by_ + 6), badge, fill=col, font=_dd_font(20, True))
    d.text((x0 + 4, y1 + 12), f"{len(pts)} graded plays · receipted", fill=DIM, font=_dd_font(13))
    img.save(path)
    return path

def _dd_chart_sport(by, path, title):
    """Hit rate by sport — v9.23.0 premium: horizontal bars + win% + units."""
    from PIL import Image, ImageDraw
    W, H = 1000, 560
    BG, LINE, TXT, DIM = (10, 20, 32), (30, 58, 92), (232, 241, 250), (139, 167, 196)
    TEAL, GOLD, RED = (45, 212, 191), (245, 197, 24), (245, 101, 101)
    img = Image.new('RGB', (W, H), BG); d = ImageDraw.Draw(img)
    _dd_chrome(d, W, H, title, "WEEKLY WHALE DEEP-DIVE")
    items = sorted(by.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))[:7]
    if not items: items = [('NO DATA', [0, 0])]
    y = 120
    row_h = min(56, (H - 200) // max(1, len(items)))
    bar_x, bar_max = 220, W - 340
    for sp, (w, l) in items:
        tot = w + l
        pct = (w / tot) if tot else 0
        col = TEAL if pct >= 0.6 else (GOLD if pct >= 0.45 else RED)
        d.text((28, y + row_h // 2 - 10), sp[:12], fill=TXT, font=_dd_font(16, True))
        d.rounded_rectangle((bar_x, y + row_h // 2 - 12, bar_x + bar_max, y + row_h // 2 + 12), radius=8, fill=(18, 34, 54))
        bl = max(10, int(pct * bar_max)) if tot else 10
        d.rounded_rectangle((bar_x, y + row_h // 2 - 12, bar_x + bl, y + row_h // 2 + 12), radius=8, fill=col)
        d.text((bar_x + bar_max + 14, y + row_h // 2 - 10), f"{w}-{l} · {pct:.0%}", fill=TXT, font=_dd_font(15, True))
        y += row_h
    d.text((28, H - 62), "bars = hit rate · graded plays only, pushes excluded from rate", fill=DIM, font=_dd_font(13))
    img.save(path)
    return path

def _dd_hero_card(stats, pts, desk, path, title):
    """Week-in-numbers cover card — v9.23.0. The deep-dive's hero image."""
    from PIL import Image, ImageDraw
    W, H = 1000, 560
    BG, TXT, DIM = (10, 20, 32), (232, 241, 250), (139, 167, 196)
    TEAL, RED, GOLD = (45, 212, 191), (245, 101, 101), (245, 197, 24)
    img = Image.new('RGB', (W, H), BG); d = ImageDraw.Draw(img)
    _dd_chrome(d, W, H, title, "WHALE MASTERCLASS")
    w, l, pu = stats.get('w', 0), stats.get('l', 0), stats.get('push', 0)
    u = stats.get('units', 0.0)
    roi = stats.get('roi', 0.0)
    rec = f"{w}-{l}" + (f"-{pu}" if pu else "")
    ucol = TEAL if u >= 0 else RED
    # giant record + units
    d.text((40, 116), rec, fill=TXT, font=_dd_font(92, True))
    us = f"{'+' if u >= 0 else ''}{u:.2f}u"
    d.text((44, 232), us, fill=ucol, font=_dd_font(60, True))
    d.text((48, 306), f"ROI {'+' if roi >= 0 else ''}{roi:.1f}% · hit rate {(w / (w + l)):.0%}" if (w + l) else "", fill=DIM, font=_dd_font(19))
    # right column: week vitals panel
    px0 = 560
    d.rounded_rectangle((px0, 110, W - 40, 340), radius=14, fill=(14, 28, 46), outline=(30, 58, 92), width=2)
    vy = 132
    def vital(k, val, col=TXT):
        nonlocal vy
        d.text((px0 + 24, vy), k, fill=DIM, font=_dd_font(15))
        d.text((px0 + 24, vy + 20), val, fill=col, font=_dd_font(21, True))
        vy += 66
    winp = pts and max(pts) or 0
    losing = pts and min(pts) or 0
    vital("BEST POINT OF THE WEEK", f"+{winp:.2f}u banked" if winp >= 0 else f"{winp:.2f}u", TEAL)
    vital("DEEPEST DIP", f"{losing:.2f}u — recovered" if (pts and pts[-1] > losing) else f"{losing:.2f}u", GOLD if (pts and pts[-1] > losing) else RED)
    desk_w = sum(1 for t in desk if t.get('result') == 'WIN'); desk_l = sum(1 for t in desk if t.get('result') == 'LOSS')
    desk_pnl = sum(float(t.get('pnl') or 0) for t in desk)
    vital("DESK WEEK (POLYMARKET)", f"{desk_w}-{desk_l} · {'+' if desk_pnl >= 0 else ''}${desk_pnl:.2f}", TEAL if desk_pnl >= 0 else RED)
    d.text((40, 368), "THE WEEK IN NUMBERS — full autopsy below:", fill=DIM, font=_dd_font(15))
    for i, t in enumerate(["•  why the winners won", "•  what the losses taught", "•  P&L curve + hit rates by sport"]):
        d.text((46, 396 + i * 34), t, fill=TXT, font=_dd_font(17))
    img.save(path)
    return path

def _dd_why(p, maxlen=170):
    a = re.sub(r'\*\*', '', str(p.get('analysis') or '')).replace('\n', ' ').strip()
    return (a[:maxlen].rstrip() + ('…' if len(a) > maxlen else '')) or 'read on file'

def _dd_fmt_pick(p):
    o = p.get('odds'); os_ = f"{int(o):+d}" if isinstance(o, (int, float)) else str(o)
    r = str(p.get('result')).lower()
    em = '✅' if r in ('won','win','✅') else ('🔄' if r.startswith('push') else '❌')
    ur = p.get('units_result') if p.get('units_result') is not None else p.get('profit') or 0
    try: us = f"{float(ur):+.2f}u"
    except Exception: us = ''
    return f"{em} **{p.get('desc','?')}** ({os_}) {us}"

def whale_deepdive_text(ps, desk, st):
    s = _dd_stats(ps)
    wk = time.strftime('%b %d', time.gmtime(time.time() - 7*86400)) + ' – ' + time.strftime('%b %d', time.gmtime())
    L = [f"🧠 **WEEKLY WHALE DEEP-DIVE — {wk}**",
         f"**The week:** {s['w']}-{s['l']}" + (f"-{s['push']}" if s['push'] else '') + f" · **{s['units']:+.2f}u** · ROI {s['roi']:+.1f}%",
         ""]
    wins = [p for p in ps if str(p.get('result')).lower() in ('won','win','✅')]
    losses = [p for p in ps if str(p.get('result')).lower() in ('lost','loss','❌')]
    if wins:
        L.append("🏆 **WHY THE WINNERS WON — every factor SHiFT weighed:**")
        for p in wins[:6]:
            L.append(_dd_fmt_pick(p))
            L.append(f"   _{_dd_why(p)}_")
    if losses:
        L.append("")
        L.append("🔬 **THE LOSSES, DISSECTED — no hiding, ever:**")
        for p in losses[:5]:
            L.append(_dd_fmt_pick(p))
            L.append(f"   _read was: {_dd_why(p, 120)}_")
    if desk:
        dw = sum(1 for t in desk if t.get('result') == 'WIN'); dl = sum(1 for t in desk if t.get('result') == 'LOSS')
        dpnl = sum(float(t.get('pnl') or 0) for t in desk)
        L += ["", f"📈 **DESK FLOOR WEEK:** {dw}-{dl} · realized **{'+' if dpnl >= 0 else ''}${dpnl:.2f}** this week"]
        for t in desk[:4]:
            L.append(f"   • {_trade_label(t)} — {t.get('result')} {'+' if (t.get('pnl') or 0) >= 0 else ''}${t.get('pnl', 0):.2f}")
    by = ' · '.join(f"{k} {v[0]}-{v[1]}" for k, v in sorted(s['by_sport'].items(), key=lambda kv: -(kv[1][0]+kv[1][1]))[:6])
    if by: L += ["", f"📊 **BY SPORT:** {by}", ""]
    L.append("_Charts attached: weekly P&L curve + hit rate by sport. This is what the desk floor looks like from inside._ 🐋")
    return '\n'.join(L)[:1950]

def sharp_weekly_text(ps):
    s = _dd_stats(ps)
    wk = time.strftime('%b %d', time.gmtime(time.time() - 7*86400)) + ' – ' + time.strftime('%b %d', time.gmtime())
    L = [f"📉 **WEEKLY SHARP BREAKDOWN — {wk}**",
         f"**The card:** {s['w']}-{s['l']} · **{s['units']:+.2f}u** · ROI {s['roi']:+.1f}%", ""]
    wins = [p for p in ps if str(p.get('result')).lower() in ('won','win','✅')]
    if wins:
        L.append("🏆 **Top reads of the week:**")
        for p in wins[:3]:
            L.append(_dd_fmt_pick(p) + f" — _{_dd_why(p, 90)}_")
    losses = [p for p in ps if str(p.get('result')).lower() in ('lost','loss','❌')]
    if losses:
        L.append(f"🔻 {len(losses)} plays didn't get there — full autopsies live in the Whale deep-dive tier.")
    L.append("_P&L curve attached. Whale sees the full autopsy with charts & every factor — this is your snapshot._ 📊")
    return '\n'.join(L)[:1950]

@tasks.loop(minutes=30)
async def weekly_deepdive_watch():
    try:
        now = time.gmtime()
        if now.tm_wday != 5 or not (20 <= now.tm_hour <= 23):  # Saturday evening ET (widened v9.22.0 — deploy-collision makeups must still fire same-night)
            return
        st = await asyncio.to_thread(get_state)
        if st is None:
            return
        wk_id = time.strftime('%Y-%m-%d', now)
        if st.get('deepdive_last') == wk_id:
            return
        guild = client.guilds[0] if client.guilds else None
        if not guild:
            return
        whale_ps = await asyncio.to_thread(_dd_week_picks, 'whale')
        sharp_ps = await asyncio.to_thread(_dd_week_picks, 'sharp')
        cutoff = time.time() - 7 * 86400
        desk = [t for t in st.get('pm_trades', []) if t.get('status') == 'settled' and t.get('settled_at') and str(t.get('settled_at'))[:10] >= time.strftime('%Y-%m-%d', time.gmtime(cutoff))]
        chw = find_channel(guild, 'weekly-deepdive') or find_channel(guild, 'whale-room')
        chs = find_channel(guild, 'sharp-room')
        import io as _io
        if chw and whale_ps:
            pts = _dd_cum(whale_ps)
            p0, p1, p2 = '/tmp/dd_hero.png', '/tmp/dd_pnl.png', '/tmp/dd_sport.png'
            _st_w = _dd_stats(whale_ps)
            await asyncio.to_thread(_dd_hero_card, _st_w, pts, desk, p0, f"WHALE WEEK — {time.strftime('%b %d', now)} EDITION")
            await asyncio.to_thread(_dd_chart_pnl, pts, p1, "WHALE WEEK — P&L CURVE")
            await asyncio.to_thread(_dd_chart_sport, _st_w['by_sport'], p2, "HIT RATE BY SPORT")
            await chw.send(whale_deepdive_text(whale_ps, desk, st))
            await chw.send(files=[discord.File(p0), discord.File(p1), discord.File(p2)])
        if chs and sharp_ps:
            p3 = '/tmp/dd_sharp.png'
            await asyncio.to_thread(_dd_chart_pnl, _dd_cum(sharp_ps), p3, "SHARP WEEK — P&L CURVE")
            await chs.send(sharp_weekly_text(sharp_ps))
            await chs.send(file=discord.File(p3))
        st['deepdive_last'] = wk_id
        await asyncio.to_thread(gh_put, 'bot_state.json', st, f'weekly deep-dive {wk_id}')
        print('[deepdive] weekly reports posted', wk_id)
    except Exception as e:
        print('weekly deep-dive error:', e)

@weekly_deepdive_watch.before_loop
async def _dd_wait():
    await client.wait_until_ready()
# ============================ SHiFT GUARANTEE ENGINE (v9.21.2) ============================
# First month red on receipted tier picks -> next month FREE, applied automatically via Stripe.
# Public proof: guarantee.json on main (tally_30d per tier + honored comps log).
@tasks.loop(hours=12)
async def guarantee_watch():
    try:
        key = os.environ.get('STRIPE_KEY', '')
        guild = client.guilds[0] if client.guilds else None
        if not key or not guild:
            return
        def sget(path):
            req = urllib.request.Request('https://api.stripe.com/v1/' + path, headers={'Authorization': f'Bearer {key}'})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        def spost(path, data):
            body = urllib.parse.urlencode(data).encode()
            req = urllib.request.Request('https://api.stripe.com/v1/' + path, data=body,
                headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/x-www-form-urlencoded'}, method='POST')
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        members = await asyncio.to_thread(gh_get_json, 'stripe_members.json') or {'members': {}}
        guar = await asyncio.to_thread(gh_get_json_main, 'guarantee.json') or {}
        guar.setdefault('honored', [])
        guar.setdefault('checked', {})
        subs = await asyncio.to_thread(sget, 'subscriptions?limit=100&status=all')
        for s in subs.get('data', []):
            cid = s.get('customer')
            info = (members.get('members') or {}).get(cid) or {}
            tier = info.get('tier')
            if not tier:
                try:
                    amt = s['items']['data'][0]['price']['unit_amount']
                    tier = {999: 'lock', 2999: 'lock', 1299: 'sharp', 4999: 'sharp', 2499: 'whale', 9999: 'whale'}.get(amt)
                except Exception:
                    tier = None
            if not tier:
                continue
            created = s.get('created', 0)
            key30 = f'{cid}:first30'
            if key30 in guar['checked'] or time.time() - created < 30 * 86400:
                continue
            ps = await asyncio.to_thread(_dd_week_picks, tier, 4000)
            w0, w1 = created, created + 30 * 86400
            tot, n = 0.0, 0
            for p in ps:
                try:
                    ts = datetime.datetime.fromisoformat(str(p.get('date', ''))).timestamp()
                except Exception:
                    continue
                if w0 <= ts <= w1 and 'result' in p:
                    n += 1
                    try:
                        tot += float(p.get('units_result') if p.get('units_result') is not None else p.get('profit') or 0)
                    except Exception:
                        pass
            guar['checked'][key30] = {'units': round(tot, 2), 'plays': n, 'at': time.strftime('%Y-%m-%d', time.gmtime())}
            if tot < 0 and n >= 5:
                try:
                    coupon = await asyncio.to_thread(spost, 'coupons', {'percent_off': 100, 'duration': 'once', 'name': 'SHiFT Guarantee - free month'})
                    await asyncio.to_thread(spost, f'customers/{cid}', {'coupon': coupon['id']})
                    note = f"{info.get('username') or cid} ({tier}) first 30d {tot:.2f}u over {n} graded plays - free month applied"
                    guar['honored'].append({'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'tier': tier, 'units': round(tot, 2), 'note': note})
                    did = info.get('discord_id')
                    if did:
                        try:
                            u = await client.fetch_user(int(did))
                            await u.send(f"\U0001F6E1\uFE0F **THE SHiFT GUARANTEE - honored.** Your first month's receipted picks finished at **{tot:.2f}u**. Your next month is **FREE** - a 100% credit is already sitting on your subscription. No forms, no asking. That's the deal.")
                        except Exception:
                            pass
                    print('[guarantee] comped:', note)
                except Exception as e:
                    print('[guarantee] comp fail:', e)
        guar['updated'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        guar['tally_30d'] = {}
        for t in ('lock', 'sharp', 'whale'):
            ps = await asyncio.to_thread(_dd_week_picks, t, 30)
            tot, n = 0.0, 0
            for p in ps:
                if 'result' not in p:
                    continue
                n += 1
                try:
                    tot += float(p.get('units_result') if p.get('units_result') is not None else p.get('profit') or 0)
                except Exception:
                    pass
            guar['tally_30d'][t] = {'units': round(tot, 2), 'plays': n}
        await asyncio.to_thread(gh_put, 'guarantee.json', guar, 'guarantee tracker refresh', 'main')
    except Exception as e:
        print('guarantee watch error:', e)

@guarantee_watch.before_loop
async def _gw_wait():
    await client.wait_until_ready()

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
        # ---- stripe.json funnel feed for the ops dashboard (owner decree 2026-07-26) ----
        # Almost-checkouts = sessions left open or expired. Written at most every 30 min.
        global _STRIPE_FEED_LAST
        if time.time() - globals().get('_STRIPE_FEED_LAST', 0) > 1800:
            try:
                t30 = int(time.time() - 30 * 86400)
                sess30 = await asyncio.to_thread(sget, f'checkout/sessions?limit=100&created[gte]={t30}')
                started = completed = 0
                abandoned = []
                for s in (sess30.get('data') or []):
                    started += 1
                    if s.get('status') == 'complete':
                        completed += 1
                    elif s.get('status') in ('open', 'expired'):
                        abandoned.append({'status': s.get('status'),
                                          'tier': (s.get('metadata') or {}).get('tier') or 'unknown',
                                          'created': time.strftime('%Y-%m-%d %H:%M', time.gmtime(s.get('created') or time.time()))})
                by_tier, mrr, trialing, past_due, canceled30 = {}, 0.0, 0, 0, 0
                for su in subs.get('data', []):
                    st_su = su.get('status')
                    it = ((su.get('items') or {}).get('data') or [{}])[0]
                    price = it.get('price') or {}
                    amt = float(price.get('unit_amount') or 0) / 100
                    interval = (price.get('recurring') or {}).get('interval')
                    monthly = amt * 4.33 if interval == 'week' else (amt if interval == 'month' else (amt / 12 if interval == 'year' else 0))
                    nick = ((price.get('nickname') or '') + ' ' + str(((it.get('plan') or {}).get('nickname')) or '')).lower()
                    pid = str(price.get('product') or '')
                    tier = ('whale' if 'whale' in nick or pid == 'prod_UwGEaHuA0vRak2'
                            else 'sharp' if 'sharp' in nick or pid == 'prod_UwGEJiXLNWzl1S'
                            else 'lock' if 'lock' in nick or pid == 'prod_UwGEGi6LdFMiQ8' else 'other')
                    if st_su in ('active', 'trialing'):
                        by_tier[tier] = by_tier.get(tier, 0) + 1
                        mrr += monthly
                        trialing += 1 if st_su == 'trialing' else 0
                    elif st_su == 'past_due':
                        past_due += 1
                    elif st_su == 'canceled' and (su.get('canceled_at') or 0) > t30:
                        canceled30 += 1
                doc = {'updated': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                       'funnel_30d': {'started': started, 'completed': completed,
                                      'abandoned': started - completed,
                                      'conversion_pct': round(completed / started * 100, 1) if started else None},
                       'abandoned_recent': abandoned[-10:][::-1],
                       'subs': {'active_or_trialing': sum(by_tier.values()), 'by_tier': by_tier,
                                'trialing': trialing, 'past_due': past_due, 'canceled_30d': canceled30,
                                'mrr_usd': round(mrr, 2)}}
                await asyncio.to_thread(gh_put, 'stripe.json', doc, 'stripe funnel feed', 'main')
                globals()['_STRIPE_FEED_LAST'] = time.time()
            except Exception as _fe:
                print('[stripe] feed:', _fe)
    except Exception as e:
        print('stripe_sync error:', e)

@tasks.loop(seconds=120)  # USAGE LAW: halves Stripe+GitHub calls; member sync still ≤2 min
async def stripe_sync():
    await _stripe_sync_once()

def pm_slip_png(lb, status='LIVE', pnl=None, title='SHiFT — POLYMARKET US BET SLIP'):
    """Shareable Polymarket bet-slip card (1200x630 PNG bytes). Pure PIL, no assets needed."""
    from PIL import Image, ImageDraw, ImageFont
    import io as _io
    W, H = 1200, 630
    bg, teal, txt, dim = (10, 14, 22), (45, 226, 196), (235, 240, 245), (140, 155, 170)
    stamp_c = {'LIVE': teal, 'WON': (46, 204, 113), 'LOST': (231, 76, 60),
               'EDGE': (88, 166, 255), 'ARB': (255, 214, 90), 'TAIL': (190, 140, 255), 'LIVE-BET': (255, 140, 60)}.get(status, teal)
    def _font(sz, bold=True):
        paths = (('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf', '/usr/share/fonts/DejaVuSans-Bold.ttf')
                 if bold else
                 ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf', '/usr/share/fonts/DejaVuSans.ttf'))
        for path in paths:
            try:
                return ImageFont.truetype(path, sz)
            except Exception:
                continue
        try:
            return ImageFont.load_default(size=sz)
        except TypeError:
            return ImageFont.load_default()
    f_sm, f_md, f_lg, f_xl = _font(26, False), _font(34), _font(46), _font(64)
    img = Image.new('RGB', (W, H), bg)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 12, H], fill=teal)
    d.rectangle([12, 0, W, 6], fill=teal)
    d.text((48, 40), title, font=f_md, fill=teal)
    import re as _re
    _plain = lambda s: _re.sub(r'[^\x00-\xFF]+', '', (s or '').replace('\u2014', '-').replace('\u2013', '-').replace('\u2019', "'").replace('\u2018', "'").replace('\u201c', '"').replace('\u201d', '"')).strip()  # keep Latin-1 (KRÜ, São), drop emoji glyphs the card fonts lack
    d.text((48, 94), (_plain(lb.get('league')) + '  ' + (lb.get('title') or ''))[:64], font=f_sm, fill=dim)
    d.text((48, 152), _plain(('SHORT ' if lb.get('short') else '') + (lb.get('outcome') or ''))[:38], font=f_xl, fill=txt)
    qty, price, stake = float(lb.get('qty', 0)), float(lb.get('price', 0)), float(lb.get('stake', 0))
    d.text((48, 252), f"{qty:g} shares @ {price:.2f} · ${stake:.2f} → pays ${qty:.2f}", font=_font(40), fill=txt)
    d.text((48, 330), f"market: {lb.get('marketSlug', '')}", font=f_sm, fill=dim)
    d.text((48, 370), f"order: {str(lb.get('order_id', ''))[:30]}  ·  placed {str(lb.get('placed_at', ''))[:19].replace('T', ' ')} UTC", font=f_sm, fill=dim)
    d.rounded_rectangle([W - 330, H - 180, W - 48, H - 84], radius=14, outline=stamp_c, width=5)
    d.text((W - 300, H - 165), status, font=f_xl, fill=stamp_c)
    if pnl is not None:
        sign = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        d.text((48, H - 160), f"P&L {sign}", font=f_lg, fill=stamp_c)
    d.text((48, H - 64), 'SHiFT DESK — real money, public receipts', font=f_sm, fill=dim)
    buf = _io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()


# ---------- PM TRADING DESK ----------
# Owner decree 2026-07-25: fully autonomous Polymarket US trading. No bet-count or size
# limits — the ONLY law is profit. Playbook from the automated-trading-bot deep dive:
# model edge (Kelly) + sum-arbitrage + tail-end yield + live divergence. Always scanning.
TRADER_ON = os.environ.get('POLYMARKET_TRADER', '') == '1'
TRADER_BANK_START = 50.0
TRADER_MIN_EDGE = 0.045     # owner decree 2026-07-25: volume up, profit still law — 4.5% bar
TRADER_LIVE_EDGE = 0.08
TRADER_ARB_SUM = 0.985
TRADER_TAIL_MIN = 0.90
TRADER_KELLY = 0.5          # fractional Kelly
TRADER_MIN_LIQUID = 0.03    # DEPLOYMENT LAW (owner decree 2026-07-29: "I don't want 15-20%
                            # sitting in raw cash — all the cash to be used; when we win it goes
                            # back out to get in another trade"). Reserve = max($2.50, 3% of roll).
# ---- NEAR-TERM LAW (same decree): "games that are either live to be bet on or very close to
# starting today within an hour — not two or three days away." Capital locked for days can't
# compound. Model entries (EDGE/TAIL) require LIVE or start <= 90 min out; ARB (risk-free
# yield) may reach 24h; anything farther is skipped at the event level.
NEAR_TERM_SECS = 90 * 60
ARB_FAR_SECS = 24 * 3600
# TAIL-WINDOW LAW (owner 7/29: "there's $26 ready — why isn't it used?"): the 90-minute
# gate was right for EDGE (edge decays with time) but wrong for TAIL — tails are the
# DEPLOYMENT vehicle: near-certainties settling TONIGHT recycle capital by tomorrow.
# Pregame tails reach 14h (same-day); live tails settle within 12h.
TAIL_FAR_SECS = 14 * 3600
# MARKET-TAIL LAW: model-less sports (KBO, cricket, UEFA early-season with no records) were
# invisible — 24 tail candidates sat idle at 11:47 while the desk waited on a model read that
# can never exist. A complete two-sided book IS the validator at extreme prices: pregame
# >= 0.94, live >= 0.95, half stake. Exchange longshot bias (favorites outrun their price)
# is the documented edge here; the tuning loop throttles it automatically if it runs cold.
# ---- BAND LAW (7/29 autopsy, 146 settles): EDGE only prints in the 0.25-0.52 underdog window
# (all-time 22-34, +$35.26, +26% ROI — tennis Elo longshots are the engine). 0.52-0.90 is the
# model-overclaim band (12-13, -$31.10); <0.25 is lotto territory (7-29, -$27.43, 80% esports).
# Sub-0.30 entries need a monster edge (>= 15pp) — the Stephens/Maestrelli profile.
EDGE_BAND = (0.25, 0.52)
EDGE_BAND_DEEP = 0.15
PM_EDGE_RETIRED = ('cs2', 'csgo')  # cs2 EDGE: 9-22, -$46.75 all-time — 8-19, -$45.67 AFTER the
                                   # 7/27 hardening. The shrink/clamp/bar didn't save it. Retired.

def _desk_room(B, expo, expo0=0.0):
    """Stake room under the DEPLOYMENT LAW (owner decree 2026-07-29): deploy the cash —
    reserve is max($2.50, 3% of the total roll), replacing the old 15% idle floor.
    B = starting cash this cycle, expo = deployed incl. this cycle's stacked intents,
    expo0 = deployed at cycle start. (v9.16.4 bug: stacked expo in both terms = ballooning.)"""
    cash_left = B - max(0.0, expo - expo0)
    return cash_left - max(2.50, TRADER_MIN_LIQUID * (B + expo0))
TRADE_CHAN = 'shift-trades'
# DESK_LINK RETIRED (owner decree 2026-07-26): no public post links to the venue — all traffic goes to STORE_PAGE.
DISCORD_INVITE = 'https://discord.gg/8bBxWUJCYT'  # verified invite used across the site — results push here
STORE_PAGE = 'https://thelineshift.github.io/SHiFTS/upgrade.html'  # universal link — giveaway + Discord + products all live here (owner decree)
DESK_DEPOSITS_EPOCH = '2026-07-24'  # desk era opened with the $50 deposit — funds-in (deposits) tracked from this date (owner decree 2026-07-26)
# TRAFFIC LAW (owner decree 2026-07-26): public posts point to OUR STORE, never to polymarket — the desk's receipts sell the store, not the venue.


# ---------- THE ODDS API — PLAYER PROPS FEED (owner-funded free tier, 2026-07-25) ----------
# Owner decree: cards mix in who-goes-deep / hits / strikeout props. Free tier = 500 credits/mo
# -> metered in state['odds_api'], hard stop at cap. Props pull ONLY on the 4pm & 8pm ET cards
# (UTC slots 20 & 00), max 2 events per scan, edge-driven (fair price vs best number).
ODDS_API_KEY = os.environ.get('THE_ODDS_API_KEY', '')
ODDS_API_BASE = 'https://api.the-odds-api.com/v4'
ODDS_API_CAP = 430
ODDS_PROP_SLOTS_UTC = {0, 20}
ODDS_PROP_MARKETS = 'batter_home_runs,batter_hits,pitcher_strikeouts'
ODDS_PROP_MAX_EVENTS = 2
_ODDS_BOX_CACHE = {}

def _odds_budget(st):
    oa = dict((st or {}).get('odds_api') or {})
    month = time.strftime('%Y-%m', time.gmtime())
    if oa.get('month') != month:
        oa = {'month': month, 'used': 0}
    return oa

def odds_api_get(path, params, oa):
    """One metered call. oa is the working budget dict; returns (json|None, oa)."""
    if not ODDS_API_KEY or oa.get('used', 0) + 3 > ODDS_API_CAP:
        return None, oa
    try:
        q = urllib.parse.urlencode({**params, 'apiKey': ODDS_API_KEY})
        req = urllib.request.Request(f'{ODDS_API_BASE}{path}?{q}', headers={'User-Agent': 'lineshift-bot'})
        with urllib.request.urlopen(req, timeout=20) as r:
            oa['used'] = oa.get('used', 0) + int(r.headers.get('x-requests-last') or 1)
            return json.load(r), oa
    except Exception as e:
        oa['used'] = oa.get('used', 0) + 1
        print('odds-api:', path, str(e)[:140])
        return None, oa

def _amer_prob(o):
    o = float(o)
    return 100.0 / (o + 100.0) if o > 0 else (-o) / ((-o) + 100.0)

def odds_mlb_props(games, now_ts, oa, slot_utc_hour):
    """Tonight's MLB props with a real number: fair prob (vig-stripped consensus) vs best price.
    Returns (candidates, oa). Edge law: >=6% on Ks/hits, >=8% on HR yes."""
    if not ODDS_API_KEY or slot_utc_hour not in ODDS_PROP_SLOTS_UTC:
        return [], oa
    evs, oa = odds_api_get('/sports/baseball_mlb/events', {'dateFormat': 'iso'}, oa)
    if not evs:
        return [], oa
    mlb_games = [g for g in games if g.get('sport') == 'mlb']
    out = []
    for ev in evs:
        if len(out) >= ODDS_PROP_MAX_EVENTS * 2:
            break
        try:
            ts = calendar.timegm(time.strptime(ev['commence_time'][:19], '%Y-%m-%dT%H:%M:%S'))
        except Exception:
            continue
        if not (now_ts + 1800 < ts < now_ts + 12 * 3600):
            continue
        g = next((gg for gg in mlb_games if norm_txt(ev.get('home_team')) == norm_txt(gg.get('home'))
                  and norm_txt(ev.get('away_team')) == norm_txt(gg.get('away'))), None)
        if not g:
            continue
        d, oa = odds_api_get(f"/sports/baseball_mlb/events/{ev['id']}/odds",
                             {'regions': 'us', 'markets': ODDS_PROP_MARKETS, 'oddsFormat': 'american'}, oa)
        if not d:
            continue
        # gather: {(market, player, point): {'over':[(price,book)], 'under':[...], 'yes':[...]}}
        bag = {}
        for bk in d.get('bookmakers') or []:
            bkey = bk.get('key') or '?'
            for mk in bk.get('markets') or []:
                mname = mk.get('key') or ''
                for o in mk.get('outcomes') or []:
                    player = o.get('description') or ''
                    if not player:
                        continue
                    side = (o.get('name') or '').lower()
                    pt = o.get('point')
                    key = (mname, player, pt)
                    bag.setdefault(key, {}).setdefault(side, []).append((o.get('price'), bkey))
        cands = []
        for (mname, player, pt), sides in bag.items():
            if mname == 'batter_home_runs':
                yes = sides.get('yes') or []
                if len(yes) < 2:
                    continue
                fair = sum(_amer_prob(pr) for pr, _ in yes) / len(yes)
                best_pr, best_bk = max(yes, key=lambda x: float(x[0]))
                edge = fair - _amer_prob(best_pr)
                if edge < -0.01:
                    continue
                cands.append({'_bar': 0.04, 'pick': f'{player} to go deep', 'odds': best_pr, 'edge': edge, 'prob': fair,
                              'market': 'home runs', 'line': None,
                              'prop': {'player': player, 'stat': 'hr', 'line': 0, 'side': 'yes'},
                              'analysis': f"longball number — books imply {fair:.0%} across the board, {best_bk} pays {fmt_odds_num(best_pr)} ({edge:.0%} over fair)"})
                continue
            over, under = sides.get('over') or [], sides.get('under') or []
            # vig-strip per book: need same book's over+under
            by_book = {}
            for pr, bk in over:
                by_book.setdefault(bk, {})['o'] = pr
            for pr, bk in under:
                by_book.setdefault(bk, {})['u'] = pr
            fs = []
            for bk, pr in by_book.items():
                if 'o' in pr and 'u' in pr:
                    a, b = _amer_prob(pr['o']), _amer_prob(pr['u'])
                    if a + b > 0:
                        fs.append(a / (a + b))
            if not fs or pt is None:
                continue
            fair_o = sum(fs) / len(fs)
            stat = 'ks' if mname == 'pitcher_strikeouts' else 'hits'
            unit = 'Ks' if stat == 'ks' else 'hits'
            for side_name, side_list, fair_s in (('over', over, fair_o), ('under', under, 1 - fair_o)):
                if not side_list:
                    continue
                best_pr, best_bk = max(side_list, key=lambda x: float(x[0]))
                edge = fair_s - _amer_prob(best_pr)
                if edge < -0.01:
                    continue
                cands.append({'_bar': 0.025, 'pick': f'{player} {"Over" if side_name == "over" else "Under"} {pt:g} {unit}',
                              'odds': best_pr, 'edge': edge, 'prob': fair_s,
                              'market': 'pitcher strikeouts' if stat == 'ks' else 'batter hits', 'line': pt,
                              'prop': {'player': player, 'stat': stat, 'line': pt, 'side': side_name},
                              'analysis': f"books hang {pt:g} — fair is {fair_s:.0%}, {best_bk} pays {fmt_odds_num(best_pr)} ({edge:.0%} over the number)"})
        cands.sort(key=lambda c: -c['edge'])
        # fire on real disagreement (>=bar); otherwise the decree backstop deals the best near-fair numbers
        _fire = [c for c in cands if c['edge'] >= c['_bar']]
        take = (_fire + [c for c in cands if c['edge'] < c['_bar']])[:2]
        for c in take:
            c.pop('_bar', None)
            out.append({'sport': 'mlb', 'vs': f"{g['away']} @ {g['home']}", 'units': 1.0, 'start': g['start'],
                        'variety': True, 'team': g['away'], 'opp': g['home'], 'side': None, 'eid': g.get('eid'),
                        **c})
    return out, oa

def _espn_box(eid):
    hit = _ODDS_BOX_CACHE.get(eid)
    if hit and time.time() - hit[0] < 900:
        return hit[1]
    try:
        req = urllib.request.Request(f'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/summary?event={eid}',
                                     headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.load(r)
        _ODDS_BOX_CACHE[eid] = (time.time(), d)
        return d
    except Exception as e:
        print('boxscore:', eid, str(e)[:120])
        return None

def _box_stat(box, player, stat):
    np_ = norm_txt(player)
    for team in ((box.get('boxscore') or {}).get('players') or []):
        for grp in team.get('statistics') or []:
            gname = (grp.get('name') or '').lower()
            keys = [str(k).lower() for k in (grp.get('keys') or grp.get('labels') or [])]
            want = None
            if stat in ('hits', 'hr') and gname == 'batting':
                want = 'h' if stat == 'hits' else 'hr'
            elif stat == 'ks' and gname == 'pitching':
                want = 'so' if 'so' in keys else 'k'
            if not want or want not in keys:
                continue
            idx = keys.index(want)
            for ath in grp.get('athletes') or []:
                nm = norm_txt((ath.get('athlete') or {}).get('displayName') or '')
                if nm and (nm in np_ or np_ in nm):
                    try:
                        return float((ath.get('stats') or [])[idx])
                    except Exception:
                        return None
    return None

def grade_prop_pick(p, away_s, home_s):
    pr = p.get('prop') or {}
    if not p.get('eid') or not pr:
        return None
    box = _espn_box(p['eid'])
    if not box:
        return None
    val = _box_stat(box, pr.get('player', ''), pr.get('stat', ''))
    if val is None:
        return None
    u = float(p['units']) if p.get('units') is not None else 1.0
    if pr.get('stat') == 'hr' and pr.get('side') == 'yes':
        won = val >= 1
        return ('WIN' if won else 'LOSS'), (profit_units(p['odds'], u) if won else -u)
    line = float(pr.get('line') or 0)
    if val == line:
        return 'PUSH', 0.0
    won = (pr.get('side') == 'over') == (val > line)
    return ('WIN' if won else 'LOSS'), (profit_units(p['odds'], u) if won else -u)

def _pm_sport_tag(evd, ev=None):
    for src in ((ev or {}).get('sport'), evd.get('sport'), (evd.get('series') or {}).get('slug'), (ev or {}).get('slug')):
        s = norm_txt(str(src or ''))
        for k in ('mlb', 'nba', 'nhl', 'nfl', 'wnba', 'ufc', 'mls', 'epl', 'ucl', 'lol', 'cs2', 'dota2', 'valorant'):
            if k in s:
                return k
    return ''

# Exchange tag slug -> ESPN league keys the model prices (ALL-SPORTS LAW, owner decree
# 2026-07-27). Generic 'soccer' spans the four soccer boards; untagged/unknown events
# keep the full slate (conservative fallback).
PM_TAG_LEAGUE = {'nba': {'nba'}, 'wnba': {'wnba'}, 'nfl': {'nfl'}, 'ncaaf': {'ncaaf'},
                 'ncaab': {'ncaab'}, 'cfl': {'cfl'}, 'mlb': {'mlb'}, 'nhl': {'nhl'},
                 'ufc': {'ufc'}, 'mls': {'mls'}, 'epl': {'epl'}, 'laliga': {'laliga'},
                 'ucl': {'ucl'}, 'soccer': {'mls', 'epl', 'laliga', 'ucl'},
                 'premier-league': {'epl'}, 'champions-league': {'ucl'},
                 'tennis': {'tennis'}, 'atp': {'tennis'}, 'wta': {'tennis'},
                 'uecl': {'uecl'}, 'uel': {'uel'}, 'lpa': {'arg1'}, 'bra': {'bra1'},
                 'brb': {'bra2'}, 'sud': {'sudam'}, 'nwsl': {'nwsl'}, 'ecu1': {'ecu1'}}
PM_SOCCER = {'mls', 'epl', 'laliga', 'ucl', 'uecl', 'uel', 'arg1', 'bra1', 'bra2',
             'swe1', 'nor1', 'nwsl', 'ecu1', 'col1', 'sudam', 'libert'}
PM_TENNIS = {'tennis'}

def _pm_event_leagues(ev):
    """League set for an exchange event from its tags; None = full slate fallback."""
    slugs = {str(t.get('slug') or '').lower() for t in (ev.get('tags') or []) if isinstance(t, dict)}
    out = set()
    for s in slugs:
        out |= PM_TAG_LEAGUE.get(s, set())
    return out or None

def _pm_rec(summary, soccer=False):
    """Record parser. Soccer summaries are W-L-D — draw-adjust to (W + 0.5D)/N so the
    log5 inputs are real strength ratings, not inflated win shares (7/27 law)."""
    if soccer:
        try:
            w, l, d = (int(x) for x in str(summary).split('-')[:3])
            n = w + l + d
            return (w + 0.5 * d) / max(1, n), n
        except Exception:
            pass
    return se_rec(summary)

def pm_sport_prob(games, team_a, team_b, leagues=None):
    """Model win prob for team_a from a matching ESPN game (log5 + splits + home adv).
    ALL-SPORTS LAW: `leagues` pins the search to the event's own sport (kills cross-
    league name collisions); record gate relaxed 6 -> 3 games so early-season slates
    price; a gate-failing match no longer poisons other candidates (return -> continue)."""
    na, nb = norm_txt(team_a), norm_txt(team_b)
    for g in games:
        if leagues and g.get('sport') not in leagues:
            continue
        gh, ga = norm_txt(g['home']), norm_txt(g['away'])
        if not ((na in gh and nb in ga) or (na in ga and nb in gh)):
            continue
        soc = g.get('sport') in PM_SOCCER
        ph_o, nh = _pm_rec((g['recs'].get('home') or {}).get('total', ''), soc)
        pa_o, naw = _pm_rec((g['recs'].get('away') or {}).get('total', ''), soc)
        if ph_o is None or pa_o is None or nh < 3 or naw < 3:
            continue
        ph_s, _ = _pm_rec((g['recs'].get('home') or {}).get('home', ''), soc)
        pa_s, _ = _pm_rec((g['recs'].get('away') or {}).get('road', ''), soc)
        ph = 0.5 * ph_o + 0.5 * (ph_s if ph_s is not None else ph_o)
        pa = 0.5 * pa_o + 0.5 * (pa_s if pa_s is not None else pa_o)
        # small-sample regression: the relaxed 3-game gate needs a shrink toward .500
        # (n/(n+6): invisible at mid-season n, heavy in week 1) — 7/27 law
        ph = 0.5 + (ph - 0.5) * (nh / (nh + 6.0))
        pa = 0.5 + (pa - 0.5) * (naw / (naw + 6.0))
        p_home = se_log5(ph, pa) + SE_HOME_ADV.get(g['sport'], 0.03)
        p_home = min(0.93, max(0.07, p_home))
        return p_home if na in gh else 1 - p_home
    return None

def pm_esport_prob(cache, team_a, team_b):
    """Model win prob for team_a via PandaScore form on a name-matched match.
    Returns (prob, league_name) — the league feeds the SCAN_NOTABLE gate (7/29 autopsy:
    minor-league esports reads are noise; lol went 10-23, -$12.75 in 3 days)."""
    na, nb = norm_txt(team_a), norm_txt(team_b)
    for m in cache.get('esp') or []:
        n1, n2 = norm_txt(m['t1']['name']), norm_txt(m['t2']['name'])
        if not ((na == n1 and nb == n2) or (na == n2 and nb == n1)):
            continue
        forms = cache.setdefault('form', {})
        out = []
        for tid, sport in ((m['t1']['id'], m['sport']), (m['t2']['id'], m['sport'])):
            k = f"{sport}:{tid}"
            if time.time() - (forms.get(k) or {}).get('ts', 0) > 7200:
                forms[k] = {'ts': time.time(), 'f': se_ps_form(tid, sport)}
            out.append((forms.get(k) or {}).get('f'))
        f1, f2 = out
        if not f1 or not f2:
            return None, None
        g1, g2 = f1['w'] + f1['l'], f2['w'] + f2['l']
        if not g1 or not g2:
            return None, None
        # DESK HARDENING LAW (7/27 autopsy): raw small-sample win-rates read 86%..100%
        # and Kelly bet the farm (Gen.G −$17.55, LOUD −$8.74). Shrink toward .500
        # (n/(n+10)) and clamp — esports form is noise-dominated, always.
        w1 = 0.5 + (f1['w'] / g1 - 0.5) * (g1 / (g1 + 10.0))
        w2 = 0.5 + (f2['w'] / g2 - 0.5) * (g2 / (g2 + 10.0))
        p1 = w1 * (1 - w2) / (w1 * (1 - w2) + w2 * (1 - w1)) if (w1 or w2) else 0.5
        p1 = min(0.80, max(0.20, p1))
        return (p1 if na == n1 else 1 - p1), (m.get('league') or '')
    return None, None

def _pm_esp_pair(cache, team_a, team_b):
    """True when both names pair-match a PandaScore-tracked match — the esports-event
    detector for model-less paths (market tails). pm_esport_prob returns (None, None)
    when form is missing even on a real pairing, and upcoming-only feeds drop live
    matches (owner 7/29: no blind 95c esports favorites — detection must not depend
    on the model having a read)."""
    na, nb = norm_txt(team_a), norm_txt(team_b)
    if not na or not nb:
        return False
    for m in cache.get('esp') or []:
        n1, n2 = norm_txt(m['t1']['name']), norm_txt(m['t2']['name'])
        if (na == n1 and nb == n2) or (na == n2 and nb == n1):
            return True
    return False

# ---- TENNIS LAW (owner decree 2026-07-28: "tennis both men and women, hockey, and
# literally any other available market — bet all of them"). ESPN carries tennis as a
# tournament shell only (7/28 probe: 0 matchups, 0 odds on the ATP/WTA scoreboards) and
# the odds-API key was never provisioned (state budget 430, used 0) — so tennis EDGE
# runs on TennisAbstract Elo: free, both tours, 500+ rated players each, overall plus
# hard/clay/grass surface ratings. ARB stays the universal backstop for every market.
def _se_get_text(url, timeout=15):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    return urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', errors='ignore')

def _ten_norm(s):
    """Tennis name key: ascii-folded, letters+spaces only, single-spaced."""
    import unicodedata
    s = unicodedata.normalize('NFKD', (s or '').lower())
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z ]', ' ', s)).strip()

def _ten_surface(title):
    """Surface hint from event/tournament text. Default hard: most of the calendar."""
    t = _ten_norm(title).replace(' ', '')
    if any(k in t for k in ('wimbledon', 'grass', 'halle', 'queens', 'mallorca', 'eastbourne', 'newport', 'hertogenbosch')):
        return 'grass'
    if any(k in t for k in ('roland', 'frenchopen', 'clay', 'montecarlo', 'madrid', 'rome', 'italianopen',
                            'hamburg', 'bastad', 'gstaad', 'kitzbuhel', 'umag', 'barcelona', 'munich',
                            'estoril', 'bucharest', 'marrakech', 'houston', 'santiago', 'buenosaires', 'riodejaneiro')):
        return 'clay'
    return 'hard'

def se_tennis_elo(cache):
    """ATP+WTA Elo tables (TennisAbstract), cached 6h in pm_cache. Stale cache beats no
    cache: a failed refresh keeps the old table rather than blinding the model."""
    ten = cache.setdefault('tennis', {})
    if time.time() - (ten.get('ts') or 0) < 21600 and ten.get('players'):
        return ten
    players, last_idx = {}, {}
    for tour, url in (('atp', 'https://www.tennisabstract.com/reports/atp_elo_ratings.html'),
                      ('wta', 'https://www.tennisabstract.com/reports/wta_elo_ratings.html')):
        try:
            h = _se_get_text(url)
        except Exception as e:
            print('se tennis elo fail', tour, str(e)[:60])
            continue
        for row in re.findall(r'<tr>(.*?)</tr>', h, re.S):
            cells = [re.sub(r'<[^>]+>', '', c).replace('&nbsp;', ' ').strip()
                     for c in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)]
            if len(cells) < 11 or not re.match(r'^\d+(\.\d+)?$', cells[3] or ''):
                continue
            def _f(i):
                try:
                    return float(cells[i]) if cells[i] else None
                except Exception:
                    return None
            nm = _ten_norm(cells[1])
            if not nm:
                continue
            players[nm] = {'elo': float(cells[3]), 'hard': _f(6), 'clay': _f(8),
                           'grass': _f(10), 'tour': tour}
            last_idx.setdefault(nm.split()[-1], []).append(nm)
    if players:
        ten.clear()
        ten.update({'players': players, 'last': last_idx, 'ts': time.time()})
    return ten if ten.get('players') else None

def _ten_lookup(ten, name):
    """Exact normalized name, else unique-last-name + first-initial fallback."""
    nm = _ten_norm(name)
    p = ten['players'].get(nm)
    if p or not nm:
        return p
    parts = nm.split()
    if len(parts) >= 2:
        cands = ten.get('last', {}).get(parts[-1]) or []
        if len(cands) == 1 and cands[0][0] == nm[0]:
            return ten['players'].get(cands[0])
    return None

def pm_tennis_prob(cache, player_a, player_b, title=''):
    """Elo win prob for player_a: 65% surface + 35% overall when both players carry a
    surface rating, else overall Elo. Clamped [0.12, 0.88] — the rating can't see
    injuries, fatigue, or retirement risk, so extremes are distrusted by design."""
    if '/' in (player_a or '') or '/' in (player_b or ''):
        return None  # doubles — singles ratings don't apply
    ten = se_tennis_elo(cache)
    if not ten:
        return None
    pa, pb = _ten_lookup(ten, player_a), _ten_lookup(ten, player_b)
    if not pa or not pb:
        return None
    surf = _ten_surface(title)
    if pa.get(surf) and pb.get(surf):
        ra = 0.65 * pa[surf] + 0.35 * pa['elo']
        rb = 0.65 * pb[surf] + 0.35 * pb['elo']
    else:
        ra, rb = pa['elo'], pb['elo']
    p = 1.0 / (1.0 + 10 ** (-(ra - rb) / 400.0))
    return min(0.88, max(0.12, p))

# ---- COMEBACK LAW (owner decree 2026-07-29): "maybe the favorite lost the first map or
# the first set — there's still a chance they come back, grab that value when the position
# is good." The pattern: pre-match favorite drops set/map 1, the live market OVERREACTS,
# price dips below the set-tree floor. We anchor every 2-way market pre-match (price p0 +
# model m0), then buy the dip when the drop exceeds what one set/map actually costs.
def _tree_p(s, a, b, n):
    """Match win prob from set/map state (a,b) with set-win prob s, first to n (iid sets)."""
    if a >= n:
        return 1.0
    if b >= n:
        return 0.0
    return s * _tree_p(s, a + 1, b, n) + (1 - s) * _tree_p(s, a, b + 1, n)

def _set_prob(m, n):
    """Set/map win prob implied by match prob m (first to n), bisection on the tree."""
    if m is None or not (0.02 < m < 0.98):
        return None
    lo, hi = 0.02, 0.98
    for _ in range(60):
        mid = (lo + hi) / 2
        if _tree_p(mid, 0, 0, n) < m:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2

def pm_kelly(p, price, B):
    """Fractional-Kelly stake for a binary buy at `price` with model prob p."""
    edge = p - price
    if edge <= 0:
        return 0.0
    return min(B * (edge / (1 - price)) * TRADER_KELLY, B * 0.5)

# ---- DESK HARDENING LAW (owner decree 2026-07-27: "analyze the losses, win more than
# we lose") — from the 55-trade autopsy: ARB +$20.28 (engine, keep), EDGE +$3.49 (thin,
# tighten), LIVE-BET −$13.35 (retire). Five loss factories killed: map-level markets,
# resting-order re-entries, uncapped same-event stacks, raw esports win-rate reads,
# and live-divergence fades.
def _desk_trade_cap(B, pmstats):
    """Max stake on ONE trade: 15% of account (floor $6) — owner decree 7/29: "if you need to
    up the cap, go ahead" (was 10%). BAND LAW + the gates carry the blowup protection now."""
    acct = float((pmstats or {}).get('account') or B or 50.0)
    return max(6.0, 0.15 * acct)

def _desk_event_cap(B, pmstats):
    """Max total stake on ONE event title (open+resting+intents): 20% of account (floor $8)
    — owner decree 7/29 (was 15%). Gen.G's $35 triple-stack still can't happen: $22 ceiling."""
    acct = float((pmstats or {}).get('account') or B or 50.0)
    return max(8.0, 0.20 * acct)

def pm_trader_scan(st):
    """One desk cycle. Returns (trade_intents, notes). Each intent: market/outcome/price/stake/kind."""
    c = _pm_client()
    if not c:
        return [], []
    bal = pm_cash_balance()
    if not bal or bal['buying_power'] < 1.05:
        return [], []
    B = bal['buying_power']
    # DESK HARDENING: two ledgers. `open_trades` (filled) drives expo — resting bids are
    # contingent, not deployed risk (the reconcile loop keeps them honest every tick, and
    # buying_power already nets their reservation). `held_trades` (open+resting) drives
    # the duplicate shield and the per-event cap — a resting bid on a contract must still
    # block re-entry (Gen.G map1 double-loss, 35 min apart).
    open_trades = [t for t in st.get('pm_trades', []) if t.get('status') == 'open']
    held_trades = [t for t in st.get('pm_trades', []) if t.get('status') in ('open', 'resting')]
    pmstats = st.get('pm_stats') or {}
    expo = sum(float(t.get('stake', 0)) for t in open_trades)
    expo0 = expo  # cycle-start deployed — intents stack on top, and they spend REAL cash
    have = {(t['market_slug'], t['outcome']) for t in held_trades}
    have_slugs = {t['market_slug'] for t in held_trades}  # one direction per market, ever
    now = time.time()
    dates = sorted({time.strftime('%Y%m%d', time.gmtime(now - 4 * 3600)),
                    time.strftime('%Y%m%d', time.gmtime(now + 20 * 3600))})
    games = []
    for d in dates:
        try:
            games += se_espn_all(d)
        except Exception:
            pass
    cache = st.setdefault('pm_cache', {})
    if now - (cache.get('esp_ts') or 0) > 7200:
        esp = []
        for gg in ('cs2', 'lol', 'valorant', 'dota2', 'ow'):
            esp += se_ps_upcoming(gg)
        cache['esp'], cache['esp_ts'] = esp, now
    fmt = lambda ts: time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts))
    # ALL-SPORTS LAW (owner decree 2026-07-27): the desk eats EVERY sport the horizon
    # carries, not just esports. The exchange ignores tagSlug (7/26 probe: a cs2 query
    # returned NBA/NFL events) and caps a page at 100 — so coverage comes from slicing
    # the -4h..+72h horizon into 3 chunks. Net cost: 3 calls/scan vs the old 6 (the
    # five dead tag fetches are retired).
    evs, _seen_ev = [], set()
    for _w0, _w1 in ((-4, 20), (20, 44), (44, 72)):
        try:
            r = c.events.list({'closed': False, 'startTimeMin': fmt(now + _w0 * 3600),
                               'startTimeMax': fmt(now + _w1 * 3600), 'limit': 100})
            for e2 in (r.get('events') if isinstance(r, dict) else r) or []:
                _id2 = e2.get('id') or e2.get('eventId')
                if _id2 and _id2 not in _seen_ev:
                    _seen_ev.add(_id2)
                    evs.append(e2)
        except Exception as e:
            print('[trader] events chunk:', str(e)[:80])
    if not evs:
        return [], []
    intents, notes = [], []
    taken_keys = set()  # intra-scan duplicate shield (event, team) across market slugs — the
                        # Frech ×2 same-minute double-entry slipped between two slugs (7/28)
    cbc = {k: v for k, v in (cache.get('cb') or {}).items() if now - float(v.get('ts') or 0) < 36 * 3600}
    cache['cb'] = cbc  # COMEBACK LAW anchors: pre-match price + model per 2-way slug (36h TTL)
    hb = {'vs': 0, 'three_way': 0, 'expo': expo, 'B': B, 'leagues': {}, 'cb': len(cbc)}
    _tune = _desk_tuning(st)
    _tb = lambda k: (_tune.get(k) or {}).get('edge_bonus', 0.0)
    _tm = lambda k: (_tune.get(k) or {}).get('stake_mult', 1.0)
    if _tune:
        hb['tuning'] = [v['why'] for v in _tune.values()]
    for ev in evs:
        title = ev.get('title') or ev.get('name') or ''
        if (' vs ' not in title.lower()) and (' vs. ' not in title.lower()):
            continue
        leagues = _pm_event_leagues(ev)  # league-pinned model reads (ALL-SPORTS LAW)
        eid = ev.get('id') or ev.get('eventId')
        try:
            det = c.events.retrieve(eid) if eid else ev
        except Exception:
            continue
        evd = (det.get('event') if isinstance(det, dict) and 'event' in det else det) or {}
        ev_start = ev.get('startTime') or ''
        try:
            from datetime import datetime as _dt
            ts_ev = _dt.fromisoformat(str(ev_start).replace('Z', '+00:00')).timestamp()
        except Exception:
            ts_ev = now + 3600
        live = ts_ev <= now
        if not live and ts_ev - now > ARB_FAR_SECS:
            continue  # NEAR-TERM LAW: far-out markets lock capital for days — skipped entirely
        outcomes = []
        dropped_winner = 0  # winner-market sides we can't name (soccer DRAW leg) — 7/25 fake-arb lesson
        draw_px = None  # book's draw-leg price — feeds the 3-way pricing path (ALL-SPORTS LAW)
        for m in evd.get('markets') or []:
            smt = str(m.get('sportsMarketType') or m.get('marketType') or '').lower()
            if 'winner' not in smt and 'moneyline' not in smt:
                continue
            _mslug = m.get('marketSlug') or m.get('slug') or ''
            if re.search(r'-(game|map)\d+', _mslug):
                continue  # esports game-N/map-N markets: our model is match-level — wrong granularity
                # (DESK HARDENING: the Gen.G −$17.55 double loss was on -map1 with a match-level read)
            md = m.get('marketMetadata') if isinstance(m.get('marketMetadata'), dict) else {}
            sides = m.get('marketSides') or []
            long_side = next((s for s in sides if s.get('long')), sides[0] if sides else {})
            team = ((long_side.get('team') or {}).get('name')) or (md or {}).get('outcome') or ''
            if not team or team.lower() == 'draw':
                dropped_winner += 1  # the DRAW leg — count it even when untradable or quoteless (7/25 Austrian lesson)
                if team.lower() == 'draw':
                    try:
                        _dp = float((long_side.get('quote') or {}).get('value') or 0)
                        draw_px = _dp if 0 < _dp < 1 else None
                    except Exception:
                        draw_px = None
                continue
            if long_side.get('tradable') is False:
                continue
            q = long_side.get('quote') or {}
            try:
                pr = float(q.get('value') or long_side.get('price') or 0)
            except Exception:
                pr = 0
            if not (0.005 < pr < 0.995):
                continue
            slug = m.get('marketSlug') or m.get('slug') or ''
            if slug:
                outcomes.append({'slug': slug, 'team': team, 'price': pr})
                # esports single-market shape: the SHORT side of a match-winner market is the
                # OPPONENT's contract (different team name) — collect it as a short buy.
                # Soccer per-team markets carry the SAME team on the short side → skipped.
                short_side = next((s for s in sides if not s.get('long')), None)
                team2 = ((short_side.get('team') or {}).get('name')) or '' if short_side else ''
                if team2 and team2 != team and short_side.get('tradable') is not False:
                    try:
                        pr2 = float((short_side.get('quote') or {}).get('value') or 0)
                    except Exception:
                        pr2 = 0
                    if 0.005 < pr2 < 0.995:
                        outcomes.append({'slug': slug, 'team': team2, 'price': pr2, 'short': True})
            else:
                dropped_winner += 1
        if len(outcomes) < 2:
            continue
        hb['vs'] += 1
        _lg = (sorted(leagues)[0] if leagues else '?')
        hb['leagues'][_lg] = hb['leagues'].get(_lg, 0) + 1
        three_way = dropped_winner > 0  # draw sport — the 2-way model and any "arb" are invalid here
        if three_way:
            hb['three_way'] += 1
        # ---- PLAYBOOK: SUM-ARB — buy every side when the book sums under $1 (risk-free).
        # ONLY on a complete two-way book: exactly 2 named sides and zero dropped legs.
        tot = sum(o['price'] for o in outcomes)
        if not three_way and len(outcomes) == 2 and 0.5 < tot <= TRADER_ARB_SUM:
            n = max(1, int(min(B * 0.30, B - 1) / tot))
            for o in sorted(outcomes, key=lambda x: x['price']):  # smallest leg first — a failed leg aborts before real exposure
                if (o['slug'], o['team']) in have:
                    continue
                _tk = (title, norm_txt(o['team']))
                if _tk in taken_keys:
                    continue
                stake = round(n * o['price'], 2)
                if stake < 0.5 or stake > _desk_room(B, expo, expo0):
                    continue
                intents.append({**o, 'qty_hint': n, 'stake': stake, 'kind': 'ARB', 'p_model': None,
                                'event': title, 'ev_start': ev_start,
                                'reason': f"book sums to {tot:.3f} — locking {(1 - tot) * 100:.1f}% before it closes"})
                taken_keys.add(_tk)
                expo += stake
            continue
        if three_way:
            # MARKET-TAIL for draw sports (7/29 deployment gap): a >= 0.94 favorite on a
            # complete 3-leg book (both sides + draw quoted) needs no model — early-season
            # UEFA qualifiers have no records to price anyway. Same-day window, half stake,
            # one side per event. Tonight's Rapid Wien / Crvena zvezda profile.
            if not live and draw_px is not None and 0.005 < draw_px < 0.995 and len(outcomes) == 2 and ts_ev - now < TAIL_FAR_SECS:
                for o in outcomes:
                    if o['price'] < 0.94:
                        continue
                    if (o['slug'], o['team']) in have or o['slug'] in have_slugs:
                        continue
                    _tk3t = (title, norm_txt(o['team']))
                    if _tk3t in taken_keys:
                        continue
                    _ev_open3 = sum(float(t.get('stake', 0)) for t in held_trades if (t.get('event') or '') == title)
                    if _ev_open3 >= _desk_event_cap(B, pmstats):
                        continue
                    stake = min(B * 0.15, B - 1, _desk_trade_cap(B, pmstats)) * 0.5 * _tm('TAIL')
                    if stake >= 0.5 and stake <= _desk_room(B, expo, expo0):
                        intents.append({**o, 'stake': round(stake, 2), 'kind': 'TAIL', 'p_model': None,
                                        'event': title, 'ev_start': ev_start,
                                        'reason': f"3-way market-tail — favorite {o['price']:.0%} on a complete book (draw {draw_px:.0%}), settling today, half size"})
                        taken_keys.add(_tk3t)
                        expo += stake
            # ALL-SPORTS LAW (owner decree 2026-07-27): draw sports get PRICED, not
            # skipped. Model rates decisive-result strength (draw-adjusted soccer
            # records via _pm_rec); outright prob = (1 - book draw) x decisive prob.
            # Guards: pre-game only, +4pp extra edge bar, half stake — the draw leg
            # makes these thinner-edge markets than clean 2-ways.
            if draw_px is None or not (0.08 < draw_px < 0.50) or len(outcomes) != 2 or live:
                continue
            if ts_ev - now > NEAR_TERM_SECS:
                continue  # NEAR-TERM LAW: draw-sport entries wait for the 90-minute window
            for o in outcomes:
                if (o['slug'], o['team']) in have:
                    continue
                if o['slug'] in have_slugs:
                    continue
                _tk3 = (title, norm_txt(o['team']))
                if _tk3 in taken_keys:
                    continue
                if sum(1 for it in intents if it.get('event') == title and it['kind'] != 'ARB') >= 2:
                    continue
                others = [x['team'] for x in outcomes if x['team'] != o['team']]
                p_dec = pm_sport_prob(games, o['team'], others[0], leagues) if others else None
                if p_dec is None:
                    continue
                pm3 = (1 - draw_px) * p_dec
                edge3 = pm3 - o['price']
                if edge3 > 0.35:
                    print(f"[trader] edge-cap: {o['team']} 3-way claims {edge3:.0%} — distrusting model read, skipping")
                    continue
                if edge3 >= TRADER_MIN_EDGE + 0.04 + _tb('EDGE'):
                    stake = min(pm_kelly(pm3, o['price'], B) * 0.5 * _tm('EDGE'), _desk_trade_cap(B, pmstats))
                    if stake >= 1.0 and stake <= _desk_room(B, expo, expo0):
                        intents.append({**o, 'stake': round(stake, 2), 'kind': 'EDGE', 'p_model': pm3,
                                        'event': title, 'ev_start': ev_start,
                                        'reason': f"3-way pricing — model {pm3:.0%} (draw-adjusted, draw leg {draw_px:.0%}) vs {o['price']:.0%}, {edge3:.0%} edge, quarter-Kelly"})
                        taken_keys.add(_tk3)
                        expo += stake
            continue
        # ---- PLAYBOOK: MODEL EDGE (Kelly) + TAIL-END yield + LIVE-YIELD
        # Event-level esports detector (owner decree 7/29: "we can't just bet favorites on
        # esports 95% — there's no value in that") — esports gets EDGE/COMEBACK/ARB only,
        # never blind tails. Tags + title keywords; the per-outcome pair match joins below.
        _evtags = {str(t.get('slug') or '').lower() for t in (ev.get('tags') or []) if isinstance(t, dict)}
        _es_ev = bool(_evtags & {'esports', 'cs2', 'csgo', 'lol', 'valorant', 'dota2', 'ow',
                                 'league-of-legends', 'counter-strike', 'overwatch', 'dota'}) \
            or bool(re.search(r'\b(cs2|csgo|counter[- ]strike|valorant|dota ?2|league of legends|overwatch)\b', (title or '').lower()))
        for o in outcomes:
            if (o['slug'], o['team']) in have:
                continue
            if o['slug'] in have_slugs:
                continue  # already positioned on this contract — never both directions
            _tk = (title, norm_txt(o['team']))
            if _tk in taken_keys:
                continue  # same team via a second market slug — already intented this scan
            # DESK HARDENING: cap TOTAL exposure per event (open+resting+intents), not
            # just this scan's intents — the Gen.G match took $35.53 across 3 slugs.
            _ev_open = sum(float(t.get('stake', 0)) for t in held_trades if (t.get('event') or '') == title)
            _ev_int = sum(float(it.get('stake', 0)) for it in intents if it.get('event') == title and it['kind'] != 'ARB')
            if _ev_open + _ev_int >= _desk_event_cap(B, pmstats):
                continue
            others = [x['team'] for x in outcomes if x['team'] != o['team']]
            pm_ = pm_sport_prob(games, o['team'], others[0], leagues) if others else None
            _esp = False
            _ten = False
            if pm_ is None and others:
                pm_, _esp_lg = pm_esport_prob(cache, o['team'], others[0])
                _esp = pm_ is not None
            if pm_ is None and others and (not leagues or (leagues & PM_TENNIS)):
                pm_ = pm_tennis_prob(cache, o['team'], others[0], title)
                _ten = pm_ is not None
            _cbk = f"{o['slug']}|{norm_txt(o['team'])}"  # per-team anchor key: long+short share a slug
            _esp_ev = _es_ev or _esp or bool(others and _pm_esp_pair(cache, o['team'], others[0]))
            _cb0 = cbc.get(_cbk)
            if _cb0 and _cb0.get('esp'):
                _esp_ev = True  # anchored as esports pre-match — the upcoming feed drops live matches
            # COMEBACK LAW anchor (owner decree 7/29): remember the pre-match consensus —
            # price, model, series length — for every set/map sport. A live dip off THIS
            # number is what the set tree later prices; without it a comeback is just hope.
            if not live and pm_ is not None and (_ten or _esp_ev) and 0.05 < o['price'] < 0.98 and len(outcomes) == 2:
                _bo = 3
                if _esp_ev:
                    for _m in cache.get('esp') or []:
                        if {norm_txt(_m['t1']['name']), norm_txt(_m['t2']['name'])} == {norm_txt(o['team']), norm_txt(others[0] if others else '')}:
                            _bo = int(_m.get('bo') or 3)
                            break
                elif _ten:
                    _bo = 5 if any(k in _ten_norm(title).replace(' ', '') for k in ('australianopen', 'roland', 'frenchopen', 'wimbledon', 'usopen')) else 3
                cbc[_cbk] = {'p0': o['price'], 'm0': pm_, 'bo': _bo, 'ts': now, 'ten': _ten, 'esp': bool(_esp_ev)}
            _book_ok = len(outcomes) == 2 and all(0.005 < float(x.get('price') or 0) < 0.995 for x in outcomes)
            edge = (pm_ - o['price']) if pm_ is not None else None
            if edge is not None and edge > 0.35 and not (live and _cb0):
                # anchored live dips bypass the cap: the set-tree floor, not stale pre-match
                # pm_, is the benchmark down a set/map (a 0.70 model vs a 0.30 dipped price
                # reads as "edge 0.40" — that's the comeback profile, not a stale line)
                print(f"[trader] edge-cap: {o['team']} claims {edge:.0%} — distrusting model read, skipping")
                continue
            if live:
                # COMEBACK LAW (owner decree 7/29: "grab the value of a favorite being down a
                # set or down a map — maybe they win two sets in a row; when it's a good
                # position, grab that value"). The pre-match anchor says who SHOULD win; the
                # live book overreacts to one lost set/map. The iid set-tree floor down 0-1
                # (bo3: s², bo5: s²(3−2s)) must still beat the dipped price by 6pp + tuning —
                # we buy the overreaction, never the collapse. No anchor = no comeback.
                _cb = cbc.get(_cbk)
                if (_cb and _cb.get('m0') is not None
                        and _cb['p0'] >= 0.58 and _cb['m0'] >= 0.55
                        and _cb['p0'] - o['price'] >= 0.15 and 0.20 <= o['price'] <= 0.55):
                    _n = max(2, (int(_cb.get('bo') or 3) + 1) // 2)  # PandaScore bo2 (OW) = first-to-2 → n=2; n=1 would zero the tree
                    _s = _set_prob(_cb['m0'], _n)
                    _fl = _tree_p(_s, 0, 1, _n) if _s is not None else None
                    if _fl is not None:
                        _fl = max(0.20, min(0.65, _fl))
                        if _fl >= o['price'] + 0.06 + _tb('EDGE'):
                            _unit = 'set' if _cb.get('ten') else 'map'
                            stake = min(pm_kelly(_fl, o['price'], B) * _tm('EDGE'), _desk_trade_cap(B, pmstats))
                            if stake >= 0.5 and stake <= _desk_room(B, expo, expo0):
                                intents.append({**o, 'stake': round(stake, 2), 'kind': 'COMEBACK', 'p_model': _fl,
                                                'event': title, 'ev_start': ev_start,
                                                'reason': f"comeback value — pre-match {_cb['p0']:.0%} favorite (model {_cb['m0']:.0%}) dipped to {o['price']:.0%} live, down a {_unit}: set-tree floor {_fl:.0%} = {_fl - o['price']:.0%} overreaction"})
                                taken_keys.add(_tk)
                                expo += stake
                                print(f"[trader] comeback: {o['team']} @ {o['price']:.2f} — anchor {_cb['p0']:.0%}, floor {_fl:.0%} ({title[:44]})")
                # LIVE-BET fades stay retired (7/27 autopsy: 3-7, −$16.55 — stale pregame model
                # vs smarter live money). LIVE-YIELD (owner decree 7/29: "games that are live to
                # be bet on") buys live near-certainties only: model-backed at price >= 0.90 with
                # model >= 0.80; MARKET-TAIL (no model — KBO/cricket/early-season UEFA) at >= 0.95
                # on a complete book. Both settle within 12h, half stake. Never a fade.
                # ESPORTS EXCLUDED (owner 7/29: "can't just bet favorites on esports 95% —
                # there's no value in that") — esports value comes from EDGE + COMEBACK now.
                _ly_model = not _esp_ev and pm_ is not None and o['price'] >= TRADER_TAIL_MIN and pm_ >= 0.80
                _ly_mkt = not _esp_ev and pm_ is None and o['price'] >= 0.95 and _book_ok
                if (_ly_model or _ly_mkt) and ts_ev - now > -12 * 3600:
                    stake = min(B * 0.10, B - 1, _desk_trade_cap(B, pmstats)) * 0.5 * _tm('TAIL')
                    if stake >= 0.5 and stake <= _desk_room(B, expo, expo0):
                        _how = f"model {pm_:.0%} confirms" if _ly_model else "market-validated (no model — complete live book)"
                        intents.append({**o, 'stake': round(stake, 2), 'kind': 'TAIL', 'p_model': pm_,
                                        'event': title, 'ev_start': ev_start,
                                        'reason': f"[live] near-certainty yield — live price {o['price']:.0%}, {_how}, settles within hours, half size"})
                        taken_keys.add(_tk)
                        expo += stake
                continue
            if o['price'] >= TRADER_TAIL_MIN + _tb('TAIL') and ts_ev - now < TAIL_FAR_SECS:
                # TAIL-WINDOW LAW: same-day tails deploy idle cash — capital returns TONIGHT.
                # ESPORTS EXCLUDED (owner 7/29): no 95c esports favorites, pregame or live.
                _tl_model = not _esp_ev and pm_ is not None and pm_ >= 0.78
                _tl_mkt = not _esp_ev and pm_ is None and o['price'] >= 0.94 and _book_ok
                if _tl_model or _tl_mkt:
                    stake = min(B * 0.15, B - 1, _desk_trade_cap(B, pmstats)) * _tm('TAIL') * (1.0 if _tl_model else 0.5)
                    if stake >= 0.5 and stake <= _desk_room(B, expo, expo0):
                        yld = (1 - o['price']) / o['price']
                        _how = f"model {pm_:.0%} confirms" if _tl_model else "market-validated (no model — complete book)"
                        intents.append({**o, 'stake': round(stake, 2), 'kind': 'TAIL', 'p_model': pm_,
                                        'event': title, 'ev_start': ev_start,
                                        'reason': f"tail-end yield — {yld * 100:.1f}% on a near-certain settling today, {_how}"})
                        taken_keys.add(_tk)
                        expo += stake
                continue
            if pm_ is None:
                continue
            # NEAR-TERM LAW: EDGE entries wait for the 90-minute window (owner 7/29) — model
            # edge decays with time; tails (above) don't, so tails deploy all day.
            if ts_ev - now > NEAR_TERM_SECS:
                continue
            # BAND LAW (7/29 autopsy, 146 settles): EDGE prints ONLY in the 0.25-0.52 underdog
            # window (+$35.26 all-time). 0.52-0.90 = overclaim band (-$31.10), <0.25 = lotto
            # (-$27.43). Sub-0.30 needs a >= 15pp monster edge (the Stephens/Maestrelli profile).
            if not (EDGE_BAND[0] <= o['price'] <= EDGE_BAND[1]):
                continue
            if o['price'] < 0.30 and edge < EDGE_BAND_DEEP:
                continue
            _sl_l = ((o.get('slug') or '') + ' ' + (title or '')).lower()
            if any(r in _sl_l for r in PM_EDGE_RETIRED):
                continue  # cs2 EDGE retired — 9-22, -$46.75 all-time; hardening didn't save it
            if _esp and not any(k in (_esp_lg or '') for k in SCAN_NOTABLE):
                continue  # minor-league esports reads are noise — notable leagues only (7/29)
            # noise bars: esports form +4pp (noise-heavy), tennis Elo +2pp (injury/retirement-blind)
            _xbar = 0.04 if _esp else (0.02 if _ten else 0.0)
            if edge >= TRADER_MIN_EDGE + _xbar + _tb('EDGE'):
                stake = min(pm_kelly(pm_, o['price'], B) * _tm('EDGE'), _desk_trade_cap(B, pmstats))
                if stake >= 0.5 and stake <= _desk_room(B, expo, expo0):
                    intents.append({**o, 'stake': round(stake, 2), 'kind': 'EDGE', 'p_model': pm_,
                                    'event': title, 'ev_start': ev_start,
                                    'reason': f"{'Elo' if _ten else 'model'} {pm_:.0%} vs market {o['price']:.0%} — {edge:.0%} edge, half-Kelly"})
                    taken_keys.add(_tk)
                    expo += stake
    non_arb = [i for i in intents if i.get('kind') != 'ARB']
    if len(non_arb) > 10:  # DEPLOYMENT LAW (7/29): 10 slots — velocity comes from count, not size
        keep = {id(i) for i in sorted(non_arb, key=lambda x: -x.get('stake', 0))[:10]}
        intents = [i for i in intents if i.get('kind') == 'ARB' or id(i) in keep]
    expo = expo0 + sum(float(i.get('stake', 0)) for i in intents)
    hb['expo'], hb['B'], hb['expo0'] = expo, B, expo0
    notes.append(hb)
    return intents, notes

def _clv_note(t):
    """Closing-line diagnostic for the autopsy — did the market move OUR way after we took
    the number? Uses the price_path snapshots pm_watch collects over the position's life."""
    path = t.get('price_path') or []
    if len(path) < 2 or not t.get('price'):
        return ''
    taken = float(t['price'])
    last = float(path[-1].get('px') or 0)
    if not last:
        return ''
    delta = (taken - last) if t.get('short') else (last - taken)  # positive = we beat the number
    cents = delta * 100
    if abs(cents) < 1:
        return ' Rode the number flat to the result.'
    if cents > 0:
        return f" Taken {taken:.2f}, rode to {last:.2f} before the result — beat the number by {cents:.0f}¢ ✅"
    return f" Taken {taken:.2f}, slid to {last:.2f} before the result — the number ran {abs(cents):.0f}¢ against us ⚠️"

def _desk_tuning(st):
    """LOSS-RESEARCH LOOP (owner decree 2026-07-26): settled-trade lessons tune the desk's bars.
    Rolling per-playbook record over the last 30 settles — a playbook running cold
    (<40% win rate AND negative P&L over 4+ settles) gets its edge bar raised +3pp and
    stakes halved until it heals. The desk adjusts itself after every loss, in writing."""
    by_kind = {}
    for t in [x for x in st.get('pm_trades', []) if x.get('status') == 'settled'][-30:]:
        k = t.get('kind') or 'EDGE'
        rec = by_kind.setdefault(k, [0, 0, 0.0])
        if t.get('result') == 'WIN':
            rec[0] += 1
        else:
            rec[1] += 1
        rec[2] += float(t.get('pnl') or 0)
    tune = {}
    for k, (w_, l_, p_) in by_kind.items():
        n = w_ + l_
        if n >= 4 and p_ < 0 and (w_ / n) < 0.40:
            tune[k] = {'edge_bonus': 0.03, 'stake_mult': 0.5,
                       'why': f'{k} cold ({w_}-{l_}, {p_:+.2f}) — bar +3pp, half size until healed'}
    return tune

def _trade_autopsy_core(t, res):
    "LOSS AUTOPSY LAW (owner decree 2026-07-25): settled trades get a post-mortem."
    kind = t.get('kind')
    pnl = float(t.get('pnl') or 0)
    p_mod, price = t.get('p_model'), t.get('price')
    if pnl >= 0:
        if kind == 'ARB':
            return f"+${pnl:.2f} — book was complete and both legs settled as priced; arb math did its job."
        if p_mod:
            return f"+${pnl:.2f} — model {p_mod:.0%} vs market {price:.0%} was real edge; thesis held."
        return f"+${pnl:.2f} — thesis held."
    if kind == 'ARB':
        return ("the book was NOT complete — a leg the scan could not see (draw) killed both sides. "
                "Guard hardened: nameless legs now count before quote/tradability gates.")
    if kind == 'TAIL':
        return ("the near-certain lost — tail yield is not free money. After 20 settled tails, "
                "recheck the 0.93 bar against the actual hit rate.")
    if kind == 'LIVE-BET':
        return ("live divergence reverted — mid-game prices whipsaw. Consider a bigger live edge bar "
                "or a smaller Kelly fraction on live plays.")
    if p_mod:
        return f"model {p_mod:.0%} vs market {price:.0%} missed — logged for the playbook review."
    return "edge thesis missed — logged for the playbook review."

def _trade_autopsy(t, res):
    """Autopsy + closing-line diagnostic. Every settle gets a researched post-mortem —
    why it won or lost, and whether the market agreed with our number along the way."""
    return _trade_autopsy_core(t, res) + _clv_note(t)

def _trade_label(t):
    """Display name for a desk position. A SHORT must never read like the team won —
    'SHORT ThunderTalk Gaming' says we faded them; the result then grades OUR ticket."""
    name = (t.get('outcome') or '') if isinstance(t, dict) else str(t)
    return f"SHORT {name}" if (isinstance(t, dict) and t.get('short')) else name

def _trade_slip(t):
    return {'league': (t.get('sport') or '').upper(), 'title': t.get('event', ''), 'outcome': t['outcome'],
            'short': bool(t.get('short')),
            'qty': t.get('qty', 0), 'price': t.get('price', 0), 'stake': t.get('stake', 0),
            'marketSlug': t.get('market_slug', ''), 'order_id': t.get('order_id', ''),
            'placed_at': t.get('ts', '')}

async def trader_channel(g0):
    """The public desk floor — everyone sees, everyone talks."""
    if not g0:
        return None
    ch = find_channel(g0, TRADE_CHAN)
    if not ch:
        try:
            ch = await g0.create_text_channel('📈shift-trades',
                topic='SHiFT trading desk — Polymarket US plays around the clock. Daily recap + record here. ⚡')
            print('[trader] created #shift-trades')
        except Exception as e:
            print('[trader] channel:', e)
    return ch


# ---------- POLYMARKET GLOBAL RAIL (polymarket.com CLOB — esports + world sports) ----------
# Armed when POLY_KEY (Polygon wallet private key) + POLYMARKET_GLOBAL=1 are set.
# Same laws as the US desk: profit only, model edge / complete-book arb / tail yield, Kelly sizing,
# every trade slip-posted, every settle autopsied. Self-tests reachability + geo on first use.
POLY_KEY = os.environ.get('POLY_KEY', '')
POLY_FUNDER = os.environ.get('POLY_FUNDER', '')
GLOBAL_ON = os.environ.get('POLYMARKET_GLOBAL', '') == '1' and bool(POLY_KEY)
GAMMA = 'https://gamma-api.polymarket.com'
CLOB_HOST = 'https://clob.polymarket.com'
_POLY2 = {'client': None, 'ok': None, 'err': ''}

def poly2_client():
    if _POLY2['client'] is not None:
        return _POLY2['client']
    if not GLOBAL_ON:
        return None
    try:
        from py_clob_client.client import ClobClient
        c = ClobClient(CLOB_HOST, key=POLY_KEY, chain_id=137, funder=POLY_FUNDER or None)
        c.set_api_creds(c.create_or_derive_api_creds())
        c.get_api_keys()  # L2 authed read — probes reachability + geo + key validity
        _POLY2['client'] = c
        _POLY2['ok'] = True
        print('[global] clob armed — L2 creds ok')
    except Exception as e:
        _POLY2['ok'] = False
        _POLY2['err'] = str(e)[:160]
        print('[global] clob init failed:', _POLY2['err'])
    return _POLY2['client']

def poly2_ok():
    if not GLOBAL_ON:
        return False
    if _POLY2['ok'] is None:
        poly2_client()
    return bool(_POLY2['ok'])

def poly2_balance():
    """USDC (collateral) balance on the global wallet."""
    try:
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
        c = poly2_client()
        if not c:
            return None
        b = c.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        return float(b.get('balance', 0)) / 1e6
    except Exception as e:
        print('[global] balance:', str(e)[:120])
        return None

def _gamma_json(path, params):
    try:
        req = urllib.request.Request(GAMMA + path + '?' + urllib.parse.urlencode(params),
                                     headers={'User-Agent': 'lineshift-bot'})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except Exception as e:
        print('[global] gamma:', path, str(e)[:120])
        return None

def poly2_events(now_ts):
    """Open esports 'vs' events with exactly one 2-outcome market each (match winner)."""
    out = []
    for tag in ('esports', 'cs2', 'league-of-legends', 'dota-2', 'valorant'):
        evs = _gamma_json('/events', {'closed': 'false', 'limit': 100, 'tag_slug': tag}) or []
        for ev in evs:
            title = ev.get('title') or ''
            if ' vs ' not in title.lower():
                continue
            for m in ev.get('markets') or []:
                try:
                    outs = json.loads(m.get('outcomes') or '[]')
                    toks = json.loads(m.get('clobTokenIds') or '[]')
                    prices = json.loads(m.get('outcomePrices') or '[]')
                except Exception:
                    continue
                if len(outs) != 2 or len(toks) != 2 or len(prices) != 2:
                    continue
                if m.get('closed') or not m.get('acceptingOrders', True):
                    continue
                try:
                    pr = [float(x) for x in prices]
                except Exception:
                    continue
                if not all(0.005 < p < 0.995 for p in pr):
                    continue
                start = ev.get('startDate') or ev.get('startTime') or ''
                try:
                    ts = calendar.timegm(time.strptime(start[:19], '%Y-%m-%dT%H:%M:%S'))
                except Exception:
                    ts = 0
                out.append({'title': title, 'start': ts, 'condition': m.get('conditionId'),
                            'slug': ev.get('slug'), 'market_id': m.get('id'),
                            'outcomes': [{'name': outs[0], 'token': toks[0], 'price': pr[0]},
                                         {'name': outs[1], 'token': toks[1], 'price': pr[1]}]})
    # dedupe by condition id (tags overlap)
    seen, ded = set(), []
    for e in out:
        k = e['condition'] or e['slug']
        if k in seen:
            continue
        seen.add(k)
        ded.append(e)
    return ded

def poly2_place(token_id, stake_usd):
    """FOK market buy of `stake_usd` USDC worth of one outcome token."""
    c = poly2_client()
    if not c:
        return {'error': 'cold'}
    try:
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        signed = c.create_market_order(MarketOrderArgs(token_id=str(token_id), amount=round(float(stake_usd), 2)))
        res = c.post_order(signed, OrderType.FOK)
        if res.get('success'):
            return {'order_id': str(res.get('orderID') or res.get('id') or 'ok')}
        return {'error': str(res.get('errorMsg') or res)[:140]}
    except Exception as e:
        return {'error': str(e)[:160]}

def poly2_check_settled(t):
    """Settled? Gamma market closed -> winner is the outcome priced 1."""
    mk = _gamma_json('/markets', {'condition_ids': t.get('condition')})
    if not mk and t.get('market_id'):
        mk = _gamma_json('/markets/' + str(t.get('market_id')), {})
    try:
        if isinstance(mk, list) and mk:
            m = mk[0]
        elif isinstance(mk, dict) and mk.get('id'):
            m = mk
        else:
            m = None
        if not m or not m.get('closed'):
            return None
        outs = json.loads(m.get('outcomes') or '[]')
        prices = [float(x) for x in json.loads(m.get('outcomePrices') or '[]')]
        if len(outs) != 2 or len(prices) != 2:
            return None
        winner = outs[0] if prices[0] >= 0.5 else outs[1]
        won = norm_txt(winner) == norm_txt(t.get('outcome'))
        return {'result': 'WIN' if won else 'LOSS', 'winner': winner}
    except Exception:
        return None

def poly2_scan(st):
    """Global playbooks on esports matches. Returns (intents, notes). Same laws as the US desk."""
    intents, notes = [], []
    if not GLOBAL_ON:
        return intents, notes
    B = poly2_balance()
    if B is None or B < 2:
        notes.append({'vs': 0, 'three_way': 0, 'expo': 0, 'B': B or 0, 'cold': True})
        return intents, notes
    now_ts = time.time()
    cache = st.setdefault('pm_cache', {})
    if now_ts - (cache.get('esp_ts') or 0) > 7200:
        esp = []
        for gg in ('cs2', 'lol', 'valorant', 'dota2'):
            esp += se_ps_upcoming(gg)
        cache['esp'], cache['esp_ts'] = esp, now_ts
    open_trades = [t for t in st.get('pm2_trades', []) if t.get('status') == 'open']
    expo = sum(float(t.get('stake') or 0) for t in open_trades)
    have = {t.get('condition') for t in open_trades} | {t.get('condition') for t in intents}
    evs = poly2_events(now_ts)
    hb = {'vs': 0, 'three_way': 0, 'expo': expo, 'B': B}
    for e in evs:
        if e['condition'] in have:
            continue
        hb['vs'] += 1
        o1, o2 = e['outcomes']
        tot = o1['price'] + o2['price']
        live = bool(e['start'] and e['start'] <= now_ts + 600)
        # ---- SUM-ARB: both outcome tokens for under a dollar (2-outcome book = complete by construction)
        if 0.5 < tot <= TRADER_ARB_SUM:
            n = max(1, int(min(B * 0.30, B - 1) / tot))
            for o in sorted(e['outcomes'], key=lambda x: x['price']):
                stake = round(n * o['price'], 2)
                if stake < 1:
                    continue
                intents.append({'kind': 'ARB', 'event': e['title'], 'condition': e['condition'], 'market_id': e['market_id'],
                                'outcome': o['name'], 'token': o['token'], 'price': o['price'], 'qty': n, 'stake': stake,
                                'reason': f"complete 2-way book sums {tot:.3f} — both sides for under a dollar"})
            continue
        # ---- MODEL EDGE (PandaScore esports form) + TAIL yield
        for o in e['outcomes']:
            other = o2 if o is o1 else o1
            p, _lg2 = pm_esport_prob(cache, o['name'], other['name'])
            edge = (p - o['price']) if p else 0
            min_edge = TRADER_LIVE_EDGE if live else TRADER_MIN_EDGE
            kind = None
            if p and edge >= min_edge:
                kind = 'LIVE-BET' if live else 'EDGE'
            elif o['price'] >= TRADER_TAIL_MIN and e['start'] and e['start'] - now_ts < 86400 and (p or 0) >= 0.80:
                kind = 'TAIL'
            if not kind:
                continue
            if kind == 'TAIL':
                stake = min(B * 0.08, B - 1)
            else:
                stake = pm_kelly(p, o['price'], B)
            if stake < 1:
                continue
            intents.append({'kind': kind, 'event': e['title'], 'condition': e['condition'], 'market_id': e['market_id'],
                            'outcome': o['name'], 'token': o['token'], 'price': o['price'],
                            'qty': round(stake / o['price'], 1), 'stake': round(stake, 2), 'p_model': p,
                            'reason': (f"{'live divergence' if kind == 'LIVE-BET' else 'model'} {p:.0%} vs market {o['price']:.0%} (edge {edge:.0%})" if kind != 'TAIL'
                                       else f"tail yield: {o['price']:.2f} resolves <24h, model {p:.0%}" if p else f"tail yield: {o['price']:.2f} resolves <24h")})
    hb['expo'], hb['B'] = expo, B
    notes.append(hb)
    return intents, notes

def desk_by_kind(trades, stats=None):
    """Settled W-L(-P) breakdown per playbook kind. ARCHIVE LAW: when stats['kind_totals'] exists
    it carries the ALL-TIME split — the hot ledger only holds the last 30 days after archiving."""
    _kt = (stats or {}).get('kind_totals')
    if _kt:
        order = ('ARB', 'EDGE', 'LIVE-BET', 'TAIL', 'LEGACY')
        return ' · '.join(f"{k} {_kt[k][0]}-{_kt[k][1]}" + (f"-{_kt[k][2]}" if len(_kt[k]) > 2 and _kt[k][2] else '')
                         for k in order if k in _kt) or '—'
    kk = {}
    for t in trades:
        if t.get('result') not in ('WIN', 'LOSS', 'PUSH'):
            continue
        k = t.get('kind') or 'EDGE'
        w, l, p = kk.get(k, (0, 0, 0))
        if t['result'] == 'WIN':
            w += 1
        elif t['result'] == 'LOSS':
            l += 1
        else:
            p += 1
        kk[k] = (w, l, p)
    order = ('ARB', 'EDGE', 'LIVE-BET', 'TAIL', 'LEGACY')
    return ' · '.join(f"{k} {kk[k][0]}-{kk[k][1]}" + (f"-{kk[k][2]}" if kk[k][2] else '')
                     for k in order if k in kk) or '—'


def desk_recap_text(st, bal, open_n):
    """Account-truth recap (owner decree 2026-07-26): record, account value, net P&L vs funds in,
    playbook split, lesson. Links to OUR STORE only — never to the venue."""
    stats = st.get('pm_stats', {})
    acct, dep, net = _desk_sync_money(st, stats, bal)
    w, l, p = stats.get('wins', 0), stats.get('losses', 0), stats.get('pushes', 0)
    trades = stats.get('trades', 0)
    settled = w + l
    wr = (w / settled * 100) if settled else 0.0
    rec = f"{w}-{l}" + (f"-{p}" if p else '')
    lines = [f"📈 **SHiFT DESK — {time.strftime('%Y-%m-%d · %H:%M UTC')}**",
             f"Record **{rec}** · win rate **{wr:.0f}%** · trades **{trades}** · open **{open_n}**"]
    if acct is not None:
        roi = (net / dep * 100) if dep else 0.0
        lines.append(f"💰 Account **${acct:.2f}** · net P&L **{'+' if net >= 0 else ''}${net:.2f}** on ${dep:.2f} in (**{'+' if roi >= 0 else ''}{roi:.0f}%**)")
    else:
        pnl = stats.get('pnl', 0.0)
        lines.append(f"💰 Net P&L **{'+' if pnl >= 0 else ''}${pnl:.2f}** realized on ${dep:.2f} in (live account feed offline)")
    lines.append(f"Playbooks: {desk_by_kind(st.get('pm_trades', []), stats)}")
    _tn = _desk_tuning(st)
    if _tn:
        lines.append(f"🧭 Tuning: {'; '.join(v['why'] for v in _tn.values())}")
    if st.get('pm_lessons'):
        lines.append(f"🔬 Latest lesson: _{st['pm_lessons'][-1]['lesson']}_")
    lines.append("Real money, public receipts — profit is the only law. ⚡")
    lines.append(f"💎 Every position, result & the War Room: {STORE_PAGE}")
    return '\n'.join(lines)


def desk_pnl_png(st, stats):
    """Cumulative desk P&L chart (1200x630 PNG bytes). Pure PIL, dark brand theme."""
    from PIL import Image, ImageDraw, ImageFont
    import io as _io
    W, H = 1200, 630
    bg, teal, txt, dim = (10, 14, 22), (45, 226, 196), (235, 240, 245), (140, 155, 170)
    up, dn = (46, 204, 113), (231, 76, 60)
    def _font(sz, bold=True):
        paths = (('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', '/usr/share/fonts/DejaVuSans-Bold.ttf')
                 if bold else ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', '/usr/share/fonts/DejaVuSans.ttf'))
        for path in paths:
            try:
                return ImageFont.truetype(path, sz)
            except Exception:
                continue
        return ImageFont.load_default()
    f_sm, f_md, f_lg = _font(24, False), _font(36), _font(56)
    settled = sorted((t for t in st.get('pm_trades', []) if t.get('result') in ('WIN', 'LOSS') and t.get('pnl') is not None),
                     key=lambda t: t.get('settled_at') or t.get('ts') or '')
    # ARCHIVE LAW: hot state holds the last 30 days — seed the curve at the archived baseline
    # (all-time stats P&L minus what the visible trades account for) so the end point stays true.
    _vis = sum(float(t['pnl']) for t in settled)
    curve = [round(float(stats.get('pnl') or 0.0) - _vis, 2)]
    for t in settled:
        curve.append(curve[-1] + float(t['pnl']))
    img = Image.new('RGB', (W, H), bg)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 12, H], fill=teal)
    d.rectangle([12, 0, W, 6], fill=teal)
    d.text((48, 36), 'SHiFT DESK — P&L', font=f_lg, fill=txt)
    pnl = stats.get('pnl', 0.0)
    w_, l_ = stats.get('wins', 0), stats.get('losses', 0)
    _dep8 = _desk_basis(stats)
    _acct8 = stats.get('account')
    _hdr8 = (f"Record {w_}-{l_}   account ${float(_acct8):.2f}   net {'+' if (float(_acct8) - _dep8) >= 0 else ''}${float(_acct8) - _dep8:.2f} on ${_dep8:.2f} in"
             if _acct8 else f"Record {w_}-{l_}   net {'+' if pnl >= 0 else ''}${pnl:.2f} realized on ${_dep8:.2f} in")
    d.text((48, 108), _hdr8, font=f_md, fill=(up if (stats.get('net') if _acct8 else pnl) >= 0 else dn))
    # plot area
    x0, x1, y0, y1 = 70, W - 60, 190, H - 90
    lo, hi = min(curve + [0.0]), max(curve + [0.0])
    rng = (hi - lo) or 1.0
    hi += rng * 0.12; lo -= rng * 0.12; rng = hi - lo
    def _y(v):
        return y1 - (v - lo) / rng * (y1 - y0)
    def _x(i):
        return x0 + (i / max(1, len(curve) - 1)) * (x1 - x0)
    # grid + zero line
    for gy in (hi - rng * 0.25, hi - rng * 0.5, hi - rng * 0.75):
        d.line([x0, _y(gy), x1, _y(gy)], fill=(28, 36, 48), width=1)
    d.line([x0, _y(0), x1, _y(0)], fill=(70, 84, 100), width=2)
    if len(curve) > 1:
        pts = [(_x(i), _y(v)) for i, v in enumerate(curve)]
        d.polygon(pts + [(pts[-1][0], _y(0)), (pts[0][0], _y(0))], fill=(20, 60, 54) if pnl >= 0 else (64, 26, 22))
        d.line(pts, fill=(up if pnl >= 0 else dn), width=4, joint='curve')
        d.ellipse([pts[-1][0] - 8, pts[-1][1] - 8, pts[-1][0] + 8, pts[-1][1] + 8], fill=(up if pnl >= 0 else dn))
        imin, imax = curve.index(min(curve)), curve.index(max(curve))
        d.text((_x(imin) - 20, _y(min(curve)) + 12), f"{min(curve):+.2f}", font=f_sm, fill=dn)
        d.text((_x(imax) - 20, _y(max(curve)) - 34), f"{max(curve):+.2f}", font=f_sm, fill=up)
    else:
        d.text((W // 2 - 260, (y0 + y1) // 2), 'curve builds as positions settle', font=f_md, fill=dim)
    d.text((48, H - 56), 'Polymarket US · profit is the only law ⚡', font=f_sm, fill=dim)
    buf = _io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


STALE_ORDER_HOURS = 6

async def _pm_sweep_stale(st):
    """STALE-SWEEP LAW (owner decree 2026-07-27: 'cash sitting in order books should be
    put to use'): cancel resting orders older than 6h — and ANY resting order whose event
    has already started, immediately (a pregame-priced bid filling mid-game is the
    LIVE-BET mistake in mechanical form). Freed buying power goes back to work the same
    cycle. Verified-safe: statuses flip only for orders confirmed off the book."""
    rs = [t for t in st.get('pm_trades', []) if t.get('status') == 'resting' and t.get('market_slug')]
    if not rs:
        return
    now = time.time()
    stale = []
    for t in rs:
        try:
            age = now - time.mktime(time.strptime((t.get('ts') or '')[:19], '%Y-%m-%dT%H:%M:%S'))
        except Exception:
            age = STALE_ORDER_HOURS * 3600 + 1  # unparseable ts -> treat as stale
        live_ev = False
        try:
            import datetime as _dtx
            live_ev = _dtx.datetime.fromisoformat(str(t.get('ev_start') or '').replace('Z', '+00:00')).timestamp() <= now
        except Exception:
            pass
        if live_ev or age > STALE_ORDER_HOURS * 3600:
            stale.append(t)
    if not stale:
        return
    c = _pm_client()
    if not c:
        return
    slugs = sorted({t['market_slug'] for t in stale})
    try:
        await asyncio.to_thread(c.orders.cancel_all, {'slugs': slugs})
    except Exception as e:
        print('[desk] stale-sweep cancel:', str(e)[:120])
        return
    # verify off the book before touching statuses — an order we failed to cancel stays
    # 'resting' and gets swept next cycle (never an untracked live order)
    still = set()
    try:
        od = await asyncio.to_thread(c.orders.list)
        for o in (od.get('orders') if isinstance(od, dict) else od) or []:
            oo = (o.get('order') if isinstance(o, dict) and 'order' in o else o) or {}
            if oo.get('marketSlug'):
                still.add(oo['marketSlug'])
    except Exception:
        return  # can't verify — leave statuses, retry next cycle
    freed, swept = 0.0, 0
    for t in stale:
        if t['market_slug'] in still:
            continue
        t['status'] = 'unfilled'
        t['note'] = (t.get('note') or '') + ' [stale-swept]'
        freed += float(t.get('stake') or 0)
        swept += 1
    if swept:
        print(f"[desk] stale-sweep: cancelled {swept} order(s) on {len(slugs)} market(s) — ${freed:.2f} back to work")
        try:
            await asyncio.to_thread(gh_put, 'bot_state.json', st, 'pm stale-sweep')
        except Exception as e:
            print('[desk] stale-sweep save:', str(e)[:80])

@tasks.loop(seconds=300)
async def pm_trader():
    """Always scanning. Entries to the desk channel; exits + P&L via pm_watch."""
    if not TRADER_ON:
        return
    try:
        st = await asyncio.to_thread(get_state)
        await _pm_sweep_stale(st)  # STALE-SWEEP LAW: dead bids out, cash back to work
        intents, notes = await asyncio.to_thread(pm_trader_scan, st)
        hb = (notes or [{}])[0]
        if notes:
            _lgmix = ' '.join(f"{k}:{v}" for k, v in sorted(hb.get('leagues', {}).items())) or 'none'
            print(f"[trader] cycle: {hb.get('vs', '?')} vs-events ({_lgmix}) · {hb.get('three_way', '?')} 3-way priced · "
                  f"{len(intents)} intents · expo ${hb.get('expo', 0):.2f} · cash ${hb.get('B', 0):.2f} · room ${_desk_room(hb.get('B', 0), hb.get('expo', 0), hb.get('expo0', hb.get('expo', 0))):.2f} · cb {hb.get('cb', 0)}")
            for _tw in (hb.get('tuning') or []):
                print(f"[trader] tuning: {_tw}")
        else:
            _bal = await asyncio.to_thread(pm_cash_balance) or {}
            print(f"[trader] standing down — buying power ${_bal.get('buying_power', 0):.2f} < $1.05 · "
                  f"${_bal.get('balance', 0):.2f} deployed · desk resumes when positions settle")
        if GLOBAL_ON:
            g_intents, g_notes = await asyncio.to_thread(poly2_scan, st)
            ghb = (g_notes or [{}])[0]
            print(f"[trader] global: {ghb.get('vs', '?')} esports matches · {len(g_intents)} intents · "
                  f"expo ${ghb.get('expo', 0):.2f}/${ghb.get('B', 0):.2f}{' · COLD' if ghb.get('cold') else ''}")
            intents += g_intents
        g0 = client.guilds[0] if client.guilds else None
        placed = False
        placed_lines = []  # NO-SPAM DECREE: entries batch into one Whale digest, never per-bet public posts
        placed_trades = []  # actual placed trade dicts for the board-play check
        bad_arb = set()  # an arb with a failed leg is naked risk — abort its remaining legs
        for t in intents:
            if t['kind'] == 'ARB' and t.get('event') in bad_arb:
                continue
            if t.get('token'):
                res = await asyncio.to_thread(poly2_place, t['token'], t['stake'])
            else:
                res = await asyncio.to_thread(pm_place_bet,
                                              {'marketSlug': t['slug'], 'price': t['price'], 'minQty': 1,
                                               'short': t.get('short')}, t['stake'])
            if 'order_id' not in res:
                if t['kind'] == 'ARB':
                    bad_arb.add(t.get('event'))
                    print('[trader] arb leg failed — remaining legs aborted:', t.get('event'))
                if res.get('error') not in ('no_liquidity', 'below_min'):
                    print('[trader] place:', t.get('slug') or t.get('event'), res.get('error'))
                continue
            if t.get('token'):
                t.update({'order_id': res['order_id'], 'status': 'open', 'rail': 'global',
                          'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
                st.setdefault('pm2_trades', []).append(t)
                st['pm2_trades'] = st['pm2_trades'][-120:]
                if 'start' not in st.setdefault('pm2_stats', {'start': 0.0, 'wins': 0, 'losses': 0, 'pnl': 0.0, 'trades': 0}):
                    st['pm2_stats']['start'] = 0.0
            else:
                t.update({'order_id': res['order_id'], 'qty': res['qty'], 'stake': res['stake'],
                          'market_slug': t.pop('slug'), 'outcome': t.pop('team'), 'status': 'open',
                          'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
                st.setdefault('pm_trades', []).append(t)
                st['pm_trades'] = st['pm_trades'][-800:]  # ARCHIVE LAW: the daily age-based archive is the trim — this is only a crash-guard (the old 120-cap would have deleted customers' 30-day results)
            placed = True
            placed_trades.append(t)
            stats = st.setdefault('pm_stats', {'start': TRADER_BANK_START, 'wins': 0, 'losses': 0, 'pnl': 0.0})
            stats['trades'] = stats.get('trades', 0) + 1
            placed_lines.append(f"• **{t['kind']}** — **{_trade_label(t)}** @ {t['price']:.2f} × {t['qty']} (${t['stake']:.2f})\n  _{t['reason']}_")
            await asyncio.sleep(1)
        if placed:
            await asyncio.to_thread(gh_put, 'bot_state.json', st, 'pm trader entries')
            # BOARD PLAY LAW (owner decree 2026-07-26, widened): entries LIVE to 24h out post to
            # #shift-trades (paid members only) so they can tail with us. One batched
            # post per cycle max; a market is announced once, ever.
            try:
                g0 = client.guilds[0] if client.guilds else None
                ch3 = await trader_channel(g0) if g0 else None
                seen = st.get('board_posted')
                if not isinstance(seen, list):
                    seen = st['board_posted'] = []
                lines = []
                for it in placed_trades:
                    if it.get('kind') == 'ARB' or not it.get('ev_start'):
                        continue
                    _slug_i = it.get('market_slug') or it.get('slug')
                    if not _slug_i:
                        continue
                    try:
                        gap = datetime.datetime.fromisoformat(str(it['ev_start']).replace('Z', '+00:00')).timestamp() - time.time()
                    except Exception:
                        continue
                    if -2 * 3600 <= gap <= 24 * 3600 and _slug_i not in seen:
                        when = time.strftime('%a %I:%M %p ET', time.gmtime(datetime.datetime.fromisoformat(str(it['ev_start']).replace('Z', '+00:00')).timestamp() - 4 * 3600))
                        lines.append(f"• **{_trade_label(it)}** @ {it.get('price', 0):.2f} × {it.get('qty', 0)} (${it.get('stake', 0):.2f}) — {str(it.get('event', ''))[:60]} · starts {when}")
                        seen.append(_slug_i)
                if lines and ch3:
                    _st = st.get('pm_stats', {})
                    _tot = _st.get('pnl', 0.0)
                    _sgn = '+' if _tot >= 0 else ''
                    _acct9 = _st.get('account'); _dep9 = _desk_basis(_st)
                    _money9 = (f"account ${float(_acct9):.2f} · net {'+' if (float(_acct9) - _dep9) >= 0 else ''}${float(_acct9) - _dep9:.2f}"
                               if _acct9 else f"net {_sgn}${_tot:.2f} realized")
                    await ch3.send("📌 **DESK BOARD PLAY — live to 24h out, tail with us:**\n" + "\n".join(lines) +
                                   f"\n\n_Desk to date: {_st.get('wins', 0)}-{_st.get('losses', 0)} · {_money9} on ${_dep9:.2f} in_")
                    st['board_posted'] = seen[-60:]
                    await asyncio.to_thread(gh_put, 'bot_state.json', st, 'board play posted')
                    print(f"[trader] board play posted ({len(lines)} far-out entries)")
            except Exception as _be:
                print('[trader] board play:', _be)
        # entries stay ledger-only (revert: whale room untouched by desk traffic)
        # ---- desk recap: shift-trades every 2h, X once a day at 8 AM ET ----
        et_now = time.gmtime(time.time() - 4 * 3600)
        today_et = time.strftime('%Y-%m-%d', et_now)
        hour_key = time.strftime('%Y-%m-%d-%H', time.gmtime())
        stats = st.setdefault('pm_stats', {'start': TRADER_BANK_START, 'wins': 0, 'losses': 0, 'pnl': 0.0})
        if time.gmtime().tm_hour % 2 == 0 and stats.get('last_recap_hour') != hour_key:
            stats['last_recap_hour'] = hour_key
            bal = await asyncio.to_thread(pm_cash_balance)
            open_n = len([t for t in st.get('pm_trades', []) if t.get('status') == 'open'])
            recap = desk_recap_text(st, bal, open_n)
            ch2 = await trader_channel(g0)
            if ch2:
                try:
                    import io as _io5
                    graph = await asyncio.to_thread(desk_pnl_png, st, stats)
                    await ch2.send(recap, file=discord.File(_io5.BytesIO(graph), filename='desk_pnl.png'))
                except Exception as _ge:
                    print('[trader] recap graph:', _ge)
                    try:
                        await ch2.send(recap)
                    except Exception:
                        pass
            # public desk feed for the site dashboard
            try:
                settled = [t for t in st.get('pm_trades', []) if t.get('result') in ('WIN', 'LOSS', 'PUSH')]
                _dep10 = _desk_basis(stats)
                desk_doc = {'updated': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                            'record': f"{stats.get('wins', 0)}-{stats.get('losses', 0)}",
                            'trades': stats.get('trades', 0), 'open': open_n,
                            'pnl': round(stats.get('pnl', 0.0), 2),
                            'account': round(bal['balance'], 2) if bal else None,
                            'deposits': _dep10,
                            'net_pnl': round(bal['balance'] - _dep10, 2) if bal else None,
                            'playbooks': desk_by_kind(st.get('pm_trades', []), stats),
                            'recent': [{'outcome': t.get('outcome'), 'short': bool(t.get('short')),
                                        'result': {'WIN': 'won', 'LOSS': 'lost', 'PUSH': 'push'}.get(t.get('result')),
                                        'pnl': round(float(t.get('pnl') or 0), 2)} for t in settled[-6:]],
                            'store': STORE_PAGE}
                await asyncio.to_thread(gh_put, 'desk.json', desk_doc, 'desk stats update', 'main')
            except Exception as _de:
                print('[trader] desk.json:', _de)
            if et_now.tm_hour == 8 and stats.get('last_recap') != today_et:
                stats['last_recap'] = today_et
                # WINNERS-ONLY LAW (owner decree 2026-07-27): X gets winning results ONLY —
                # the recap posts on GREEN days (today P&L > 0, net-positive overall),
                # win-framed (no loss column). All results still post to Discord receipts.
                _dep11 = _desk_basis(stats)
                _acct11 = float(stats.get('account') or (bal or {}).get('balance') or _dep11)
                _anchor11 = float(stats.get('day_anchor') or _acct11)
                _today_pnl = _acct11 - _anchor11
                _net11 = _acct11 - _dep11
                if _today_pnl > 0 and _net11 > 0:
                    _pl1, _pl2, _pl3 = _desk_portfolio_lines(stats, st)
                    xt_recap = (f"✅ SHiFT DESK — green day {today_et}\n\n"
                                f"{_pl1}\n{_pl2}\n{_pl3}\n\n"
                                f"Real money, public receipts. ⚡\n💎 {STORE_PAGE}")
                    try:
                        await asyncio.to_thread(x_post, xt_recap[:400], None)
                    except Exception:
                        pass
                else:
                    print(f"[trader] X recap skipped (red day / net negative) — Discord receipts carry it, per WINNERS-ONLY LAW")
            await asyncio.to_thread(gh_put, 'bot_state.json', st, 'desk recap')
    except Exception as e:
        print('[trader]', e)


PM_KEEP_SETTLED_DAYS = 30  # ARCHIVE LAW (owner decree 2026-07-26): customers need the last 30 days
PM_KEEP_SETTLED_MIN = 10     # never strip the ledger bare — the 10 freshest results always stay visible
PM_KEEP_UNFILLED_DAYS = 7    # unfilled/cancelled orders were never money — archive after a week

def _pm_archive(st):
    """Settled trades older than 30 days (and week-old unfilled noise) move out of bot_state into
    pm_archive.json — every loop's state read stays lean while every surface keeps the last 30 days
    of results. Counters are untouched: record/P&L stay all-time, the playbook split lives on in
    stats['kind_totals'], and the P&L chart seeds its curve from the archived baseline. Once/day."""
    today = time.strftime('%Y-%m-%d', time.gmtime())
    stats = st.get('pm_stats') or {}
    if stats.get('last_archive') == today:
        return False
    stats['last_archive'] = today
    trades = st.get('pm_trades') or []
    now = time.time()
    def _age(t, key):
        try:
            return (now - datetime.datetime.fromisoformat(str(t.get(key) or '').replace('Z', '+00:00')).timestamp()) / 86400
        except Exception:
            return 0.0
    settled = [t for t in trades if t.get('status') == 'settled']
    # backfill the all-time playbook split BEFORE the first archive run can remove anything
    if 'kind_totals' not in stats:
        kt = {}
        for t in settled:
            if t.get('result') not in ('WIN', 'LOSS', 'PUSH'):
                continue
            k = t.get('kind') or 'EDGE'
            v = kt.setdefault(k, [0, 0, 0])
            v[{'WIN': 0, 'LOSS': 1, 'PUSH': 2}[t['result']]] += 1
        stats['kind_totals'] = kt
    old_settled = {t.get('order_id') for t in settled if t.get('settled_at') and _age(t, 'settled_at') > PM_KEEP_SETTLED_DAYS}
    fresh = sorted((t for t in settled if t.get('settled_at')), key=lambda x: str(x.get('settled_at')), reverse=True)[:PM_KEEP_SETTLED_MIN]
    old_settled -= {t.get('order_id') for t in fresh}
    old_unfilled = {t.get('order_id') for t in trades if t.get('status') == 'unfilled' and _age(t, 'ts') > PM_KEEP_UNFILLED_DAYS}
    doomed = old_settled | old_unfilled
    move = [t for t in trades if t.get('order_id') in doomed]
    # LEAN CACHE LAW: team-form snapshots refetch after 2h — anything older than a day is dead weight
    _forms = (st.get('pm_cache') or {}).get('form') or {}
    _stale = [k for k, v in _forms.items() if now - float((v or {}).get('ts') or 0) > 86400]
    for k in _stale:
        _forms.pop(k, None)
    if _stale:
        print(f'[desk] cache trim: {len(_stale)} stale form snapshots out ({len(_forms)} kept)')
    if not move and not _stale:
        return False
    stats['archived'] = int(stats.get('archived') or 0) + len(move)
    st['pm_trades'] = [t for t in trades if t.get('order_id') not in doomed]
    if move:
        try:
            arch = gh_get_json('pm_archive.json') or {'trades': []}
            have = {t.get('order_id') for t in arch.get('trades', [])}
            arch['trades'] = arch.get('trades', []) + [t for t in move if t.get('order_id') not in have]
            arch['updated'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            gh_put('pm_archive.json', arch, f"pm archive: +{len(move)} trades ({len(arch['trades'])} total)")
        except Exception as _ae:
            print('[desk] archive write:', str(_ae)[:120])
        print(f"[desk] archived {len(move)} trades ({len(st['pm_trades'])} left in hot state)")
    return True

@tasks.loop(seconds=600)
async def pm_watch():
    """Settle desk trades (and legacy challenge bets). Results to the desk floor + #receipts + X."""
    st = await asyncio.to_thread(get_state)
    # GRADING CORRECTIONS drain — honesty is the brand; a correction posts before anything else.
    pend = st.get('pending_corrections') or []
    if pend:
        _g0 = client.guilds[0] if client.guilds else None
        _rch = find_channel(_g0, 'receipts') if _g0 else None
        _dch = find_channel(_g0, TRADE_CHAN) if _g0 else None
        for _note in pend:
            for _tgt in (_rch, _dch):
                if _tgt:
                    try:
                        await _tgt.send(_note)
                    except Exception as _ce:
                        print('[desk] correction post:', _ce)
        st['pending_corrections'] = []
        await asyncio.to_thread(gh_put, 'bot_state.json', st, 'grading corrections posted')
    # RECONCILE LAW (owner decree 2026-07-26): the exchange is the truth. A ledger-open
    # trade with no exchange position is either cashed out (settle with realized P&L and
    # announce it) or an unfilled/cancelled order (never money — marked, out of stats).
    _rec = [t for t in st.get('pm_trades', []) if t.get('status') in ('open', 'resting')]
    if _rec:
        try:
            _c3 = await asyncio.to_thread(_pm_client)
            if _c3:
                _pos = await asyncio.wait_for(asyncio.to_thread(_c3.portfolio.positions), 25)
                _pd = (_pos.get('positions') if isinstance(_pos, dict) else _pos) or {}
                _held = {ms: float(p.get('netPosition') or 0) for ms, p in _pd.items()}
                _od = await asyncio.wait_for(asyncio.to_thread(_c3.orders.list), 25)
                _ol = (_od.get('orders') if isinstance(_od, dict) else _od) or []
                _resting = set()
                for _o in _ol:
                    _oo = (_o.get('order') if isinstance(_o, dict) and 'order' in _o else _o) or {}
                    if isinstance(_oo, dict) and _oo.get('marketSlug'):
                        _resting.add(_oo.get('marketSlug'))
                _rc = False
                for t in _rec:
                    slug = t.get('market_slug')
                    if not slug:
                        continue
                    if abs(_held.get(slug, 0)) >= 0.01:
                        if t.get('status') == 'resting':
                            t['status'] = 'open'; _rc = True
                        continue
                    if slug in _resting:
                        if t.get('status') != 'resting':
                            t['status'] = 'resting'; _rc = True
                            print(f"[desk] {slug}: order resting (unfilled) — out of expo")
                        continue
                    try:
                        # RESOLUTION-FIRST LAW (owner decree 2026-07-26): a vanished position is usually a
                        # RESOLVED market. Grade from the exchange ledger and let the main settle loop
                        # below post the receipt this same tick — never guess from trade fills first.
                        _res12 = await asyncio.wait_for(asyncio.to_thread(pm_check_settled, {'marketSlug': slug, 'outcome': t.get('outcome'), 'qty': t.get('qty'), 'short': t.get('short'), 'stake': t.get('stake')}), 45)
                        if _res12:
                            print(f"[desk] {slug}: resolved on-exchange — settle loop grades it this tick")
                            continue
                        _r = await asyncio.wait_for(asyncio.to_thread(_c3.portfolio.activities, {'marketSlug': [slug], 'types': ['ACTIVITY_TYPE_TRADE']}), 25)
                        _acts = (_r.get('activities') if isinstance(_r, dict) else _r) or []
                        # EXIT-FILL LAW: a long's exit is SELL fills, a short's exit is BUY fills — and
                        # only fills AFTER our entry count. A short's own entry sell is NOT an exit
                        # (the bug that buried 3 wins and zeroed 5 results on 2026-07-25/26).
                        _exit_side = 'ORDER_SIDE_BUY' if t.get('short') else 'ORDER_SIDE_SELL'
                        _entry_ts = str(t.get('ts') or '')
                        _proceeds = 0.0
                        _fills = 0
                        for _a in _acts:
                            _agg = (_a.get('trade') or {}).get('aggressorExecution') or {}
                            _oo2 = _agg.get('order') or {}
                            if _oo2.get('side') != _exit_side:
                                continue
                            _fts = str(_agg.get('transactTime') or _oo2.get('lastTransactTime') or '')
                            if _entry_ts and _fts and _fts <= _entry_ts:
                                continue
                            _proceeds += float((_oo2.get('price') or {}).get('value') or 0) * float(_oo2.get('quantity') or 0)
                            _fills += 1
                        _sell = _fills > 0
                        if _sell:
                            proceeds = round(_proceeds, 2)
                            pnl = round(proceeds - float(t.get('stake') or 0), 2)
                            res = {'result': 'WIN' if pnl > 0 else 'LOSS', 'payout': proceeds, 'pnl': pnl}
                            t.update({'status': 'settled', 'result': res['result'], 'payout': proceeds, 'pnl': pnl,
                                      'cashout': True, 'settled_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
                            lesson = _trade_autopsy(t, res) + f" (cashed out early at ${proceeds:.2f})"
                            t['autopsy'] = lesson
                            t.pop('price_path', None)  # LEAN LEDGER: snapshots fuel open-trade CLV only — dead weight once settled
                            st.setdefault('pm_lessons', []).append({'ts': t['settled_at'], 'kind': t.get('kind'), 'outcome': t.get('outcome'),
                                                                    'result': t['result'], 'pnl': pnl, 'lesson': lesson})
                            st['pm_lessons'] = st['pm_lessons'][-30:]
                            stats0 = st.setdefault('pm_stats', {'start': TRADER_BANK_START, 'wins': 0, 'losses': 0, 'pnl': 0.0})
                            stats0['pnl'] = round(stats0.get('pnl', 0.0) + pnl, 2)
                            stats0['wins'] = stats0.get('wins', 0) + (1 if pnl > 0 else 0)
                            stats0['losses'] = stats0.get('losses', 0) + (0 if pnl > 0 else 1)
                            if 'kind_totals' in stats0:
                                _ktv0 = stats0['kind_totals'].setdefault(t.get('kind') or 'EDGE', [0, 0, 0])
                                _ktv0[0 if pnl > 0 else 1] += 1
                            em0 = '🎯' if pnl > 0 else '❌'
                            sign0 = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
                            _g9 = client.guilds[0] if client.guilds else None
                            _bal0 = await asyncio.to_thread(pm_cash_balance)
                            _desk_sync_money(st, stats0, _bal0)
                            _msg0 = (f"💵 **DESK CASH-OUT {em0}:** **{_trade_label(t)}** @ {t['price']:.2f} × {t['qty']}\n"
                                     f"Cashed out early at **${proceeds:.2f}** — profit **{sign0}**\n"
                                     f"📈 Desk to date: **{stats0.get('wins', 0)}-{stats0.get('losses', 0)}**\n"
                                     + _desk_bankroll_txt(stats0, _bal0))
                            for _tgt9 in (find_channel(_g9, 'receipts') if _g9 else None, await trader_channel(_g9) if _g9 else None):
                                if _tgt9:
                                    try:
                                        await _tgt9.send(_msg0)
                                    except Exception as _pe:
                                        print('[desk] cash-out post:', _pe)
                            print(f"[desk] cash-out reconciled: {slug} ${proceeds:.2f} ({sign0})")
                        else:
                            t.update({'status': 'unfilled', 'result': None, 'pnl': 0.0,
                                      'autopsy': 'order never filled — cancelled; no money moved.'})
                            print(f"[desk] {slug}: unfilled/cancelled — out of stats")
                        _rc = True
                    except Exception as _re:
                        print('[desk] reconcile market:', str(_re)[:120])
                if _rc:
                    await asyncio.to_thread(gh_put, 'bot_state.json', st, 'desk reconcile')
        except Exception as _re2:
            print('[desk] reconcile feed:', str(_re2)[:120])
    # resting orders lock real cash too — counting only 'open' let the desk re-enter the
    # same contract 35 min later (Gen.G map1 double-loss) — DESK HARDENING LAW
    open_trades = [t for t in st.get('pm_trades', []) if t.get('status') in ('open', 'resting')]
    pmstats = st.get('pm_stats') or {}
    open_bets = [b for b in st.get('pm_live', []) if not b.get('result')]
    open_global = [t for t in st.get('pm2_trades', []) if t.get('status') == 'open']
    if not open_trades and not open_bets and not open_global:
        return
    guild = client.guilds[0] if client.guilds else None
    ch = find_channel(guild, 'receipts') if guild else None
    desk = find_channel(guild, 'receipts') if (guild and open_trades) else None  # every desk receipt lands in #receipts (owner decree 2026-07-25)
    changed = False

    # ---- desk trades ----
    stats = st.setdefault('pm_stats', {'start': TRADER_BANK_START, 'wins': 0, 'losses': 0, 'pnl': 0.0})
    for t in open_trades:
        # CLV price path (loss-research loop): snapshot our side's quote, ~2/hour/trade max
        try:
            _path = t.setdefault('price_path', [])
            if not _path or (time.time() - int(_path[-1].get('ts') or 0)) > 1800:
                _c2 = await asyncio.to_thread(_pm_client)
                if _c2:
                    _r2 = await asyncio.wait_for(asyncio.to_thread(_c2.markets.retrieve_by_slug, t['market_slug']), 25)
                    _mk2 = (_r2.get('market') if isinstance(_r2, dict) and 'market' in _r2 else _r2) or {}
                    _want_long = not t.get('short')
                    for _s0 in (_mk2.get('marketSides') or []):
                        if bool(_s0.get('long')) == _want_long:
                            _px = float(((_s0.get('quote') or {}).get('value')) or 0)
                            if _px > 0:
                                _path.append({'ts': int(time.time()), 'px': _px})
                                t['price_path'] = _path[-24:]
                                changed = True
        except Exception:
            pass
        try:
            res = await asyncio.wait_for(asyncio.to_thread(pm_check_settled, {'marketSlug': t['market_slug'], 'outcome': t['outcome'], 'qty': t['qty'], 'short': t.get('short'), 'stake': t.get('stake')}), 45)
        except Exception as e:
            print(f"[desk] settle check: {e}"); continue
        if not res:
            continue
        t['status'] = 'settled'; t['result'] = res['result']; t['payout'] = res['payout']
        t['settled_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        pnl = round(float(res['pnl']), 2) if res.get('pnl') is not None else round(float(res['payout']) - float(t['stake']), 2)
        t['pnl'] = pnl
        lesson = _trade_autopsy(t, res)
        t['autopsy'] = lesson
        t.pop('price_path', None)  # LEAN LEDGER (see reconcile)
        st.setdefault('pm_lessons', []).append({'ts': t['settled_at'], 'kind': t['kind'], 'outcome': t.get('outcome'),
                                                'result': t['result'], 'pnl': pnl, 'lesson': lesson})
        st['pm_lessons'] = st['pm_lessons'][-30:]
        stats['pnl'] = round(stats.get('pnl', 0.0) + pnl, 2)
        stats['wins'] = stats.get('wins', 0) + (1 if res['result'] == 'WIN' else 0)
        stats['losses'] = stats.get('losses', 0) + (0 if res['result'] == 'WIN' else 1)
        if 'kind_totals' in stats:  # ARCHIVE LAW: the all-time playbook split survives archiving
            _ktv = stats['kind_totals'].setdefault(t.get('kind') or 'EDGE', [0, 0, 0])
            _ktv[{'WIN': 0, 'LOSS': 1, 'PUSH': 2}.get(res['result'], 1)] += 1
        bal = await asyncio.to_thread(pm_cash_balance)
        # SETTLE-LEDGER LAW (owner decree 2026-07-27): the exchange credits settlements
        # with a lag — a batch of settles all read the SAME pre-credit balance, so result
        # posts repeated one number. Equity moves by EXACTLY pnl per settle, so we run
        # the ledger ourselves and re-anchor whenever a fresh API read already reflects it.
        _prev_acct = stats.get('account')
        _expected = round(float(_prev_acct) + pnl, 2) if _prev_acct is not None else None
        if bal and _expected is not None and abs(float(bal['balance']) - _expected) <= 0.75:
            _acct = round(float(bal['balance']), 2)  # exchange already credited — fresh read wins
        elif _expected is not None:
            _acct = _expected  # ledger carries the truth through the credit lag
        elif bal:
            _acct = round(float(bal['balance']), 2)
        else:
            _acct = None
        if _acct is not None:
            stats['account'] = _acct
            stats['net'] = round(_acct - _desk_basis(stats), 2)
            bal = {'balance': _acct, 'buying_power': (bal or {}).get('buying_power', 0.0)}
        _desk_sync_money(st, stats, bal)
        em = '✅' if res['result'] == 'WIN' else '❌'
        sign = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        slip = None
        try:
            slip = await asyncio.to_thread(pm_slip_png, _trade_slip(t),
                                           'WON' if res['result'] == 'WIN' else 'LOST', pnl,
                                           'SHiFT TRADING DESK — RESULT')
        except Exception as se:
            print('[desk] slip:', se)
        _pl1, _pl2, _pl3 = _desk_portfolio_lines(stats, st)
        line = (f"📈 **DESK RESULT {em}:** **{_trade_label(t)}** @ {t['price']:.2f} × {t['qty']} — **{res['result']} {sign}**\n"
                f"_{t.get('reason', '')}_\n"
                f"🔬 **Autopsy:** {lesson}\n"
                f"**{_pl1}**\n**{_pl2}**\n{_pl3}")
        if desk:
            try:
                import io as _io5
                if slip:
                    await desk.send(line, file=discord.File(_io5.BytesIO(slip), filename='result.png'))
                else:
                    await desk.send(line)
            except Exception:
                pass
        # CASH-OUT LAW (owner decree 2026-07-26): every settle also announces on the desk floor
        # (#shift-trades, paid-only): amount cashed, profit, running total to date.
        try:
            _floor = await trader_channel(guild) if guild else None
            if _floor and (not desk or _floor.id != desk.id):
                _msg2 = (f"💵 **DESK CASH-OUT {em}:** **{_trade_label(t)}** @ {t['price']:.2f} × {t['qty']}\n"
                         f"Cashed out **${res.get('payout', 0):.2f}** — profit **{sign}**\n"
                         f"{_pl1}\n{_pl2}\n{_pl3}")
                await _floor.send(_msg2)
        except Exception as _fe2:
            print('[desk] cash-out post:', _fe2)
        # X exposure law (owner decree 2026-07-25): desk WINNERS post to X with the record + funnel.
        # PORTFOLIO-CARD LAW (owner decree 2026-07-27): X results show the app card —
        # account balance, today's P&L %, the $64 deposit + net, open positions.
        if res['result'] == 'WIN':
            _pl1, _pl2, _pl3 = _desk_portfolio_lines(stats, st)
            xt = (f"✅ WIN {sign} — {_trade_label(t)} @ {t['price']:.2f}\n\n"
                  f"{_pl1}\n{_pl2}\n{_pl3}\n\n"
                  f"🐋 Desk floor + War Room: {STORE_PAGE}")
            try:
                await asyncio.to_thread(x_post, xt[:400], None)
            except Exception as xe:
                print('[desk] x winner:', xe)

        changed = True
        await asyncio.sleep(1)

    # ---- GLOBAL rail trades (polymarket.com) ----
    if open_global:
        stats2 = st.setdefault('pm2_stats', {'start': 0.0, 'wins': 0, 'losses': 0, 'pnl': 0.0, 'trades': 0})
        desk = desk or (await trader_channel(guild) if guild else None)
        for t in open_global:
            try:
                res = await asyncio.to_thread(poly2_check_settled, t)
            except Exception as e:
                print(f"[desk] global settle check: {e}"); continue
            if not res:
                continue
            t['status'] = 'settled'; t['result'] = res['result']
            t['settled_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            pnl = round(float(t['qty']) - float(t['stake']), 2) if res['result'] == 'WIN' else -float(t['stake'])
            t['pnl'] = pnl
            lesson = _trade_autopsy(t, res)
            t['autopsy'] = lesson
            st.setdefault('pm_lessons', []).append({'ts': t['settled_at'], 'kind': t['kind'], 'outcome': t.get('outcome'),
                                                    'result': t['result'], 'pnl': pnl, 'lesson': lesson})
            st['pm_lessons'] = st['pm_lessons'][-30:]
            stats2['pnl'] = round(stats2.get('pnl', 0.0) + pnl, 2)
            stats2['wins'] = stats2.get('wins', 0) + (1 if res['result'] == 'WIN' else 0)
            stats2['losses'] = stats2.get('losses', 0) + (0 if res['result'] == 'WIN' else 1)
            em = '🎯' if res['result'] == 'WIN' else '❌'
            sign = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
            slip = None
            try:
                slip = await asyncio.to_thread(pm_slip_png, _trade_slip(t),
                                               'WON' if res['result'] == 'WIN' else 'LOST', pnl,
                                               'SHiFT TRADING DESK — RESULT (GLOBAL)')
            except Exception as se:
                print('[desk] slip:', se)
            line = (f"🌐 **DESK RESULT {em} — GLOBAL:** **{_trade_label(t)}** @ {t['price']:.2f} × {t['qty']} — **{res['result']} {sign}**\n"
                    f"_{t.get('reason', '')}_\n"
                    f"🔬 **Autopsy:** {lesson}\n"
                    f"Global record **{stats2.get('wins', 0)}-{stats2.get('losses', 0)}** · P&L **{'+' if stats2.get('pnl', 0) >= 0 else ''}${stats2.get('pnl', 0):.2f}**")
            if desk:
                try:
                    import io as _io6
                    if slip:
                        await desk.send(line, file=discord.File(_io6.BytesIO(slip), filename='result.png'))
                    else:
                        await desk.send(line)
                except Exception:
                    pass
            # NO-SPAM DECREE: global rail follows the same law — recap carries the record.

            changed = True
            await asyncio.sleep(1)

    # ---- legacy challenge bets (rail retired; settle stragglers honestly) ----
    for lb in open_bets:
        try:
            res = await asyncio.to_thread(pm_check_settled, lb)
        except Exception as e:
            print(f"[pm] settle check: {e}"); continue
        if not res:
            continue
        lb['result'] = res['result']; lb['payout'] = res['payout']
        lb['settled_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        pnl = round(float(res['payout']) - float(lb['stake']), 2)
        bal = await asyncio.to_thread(pm_cash_balance)
        em = '🎯' if res['result'] == 'WIN' else '❌'
        sign = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        if ch:
            try:
                await ch.send(f"🧾 **PM RESULT (legacy challenge):** [{lb.get('league', '')}] **{_trade_label(lb)}** @ {lb['price']:.2f} {em} **{res['result']}** {sign}\n"
                              f"Real money · stake ${lb['stake']:.2f} → paid ${res['payout']:.2f} · Polymarket US\n"
                              + _desk_bankroll_txt(st.get('pm_stats') or {}, bal))
            except Exception:
                pass
        changed = True
        await asyncio.sleep(1)
    changed = _pm_archive(st) or changed  # ARCHIVE LAW: hot state carries the last 30 days only
    if changed:
        await asyncio.to_thread(gh_put, 'bot_state.json', st, 'pm settle receipts')


@tasks.loop(seconds=900)
async def wallet_watch():
    """Refresh on-chain wallet balances + Polymarket snapshot for the ops dashboard."""
    try:
        bal = await asyncio.to_thread(wallet_balances)
        await asyncio.to_thread(gh_put, 'wallet_balances.json', bal, 'wallet balances')
        pmst = await asyncio.to_thread(polymarket_status)
        await asyncio.to_thread(gh_put, 'polymarket.json', pmst, 'polymarket status')
    except Exception as e:
        print('wallet_watch error:', e)

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
        # ONE-TIME per version: X bio advertises the league watchlist (owner decree 2026-07-26).
        _stb = await asyncio.to_thread(get_state)
        if _stb is not None and _stb.get('x_bio_v') != X_BIO_V:
            try:
                await asyncio.to_thread(x_update_bio, X_BIO_TEXT)
                _stb['x_bio_v'] = X_BIO_V
                await asyncio.to_thread(gh_put, 'bot_state.json', _stb, f'x bio v{X_BIO_V}: league watchlist')
                print('[audit] x bio updated')
            except Exception as _be:
                print('[audit] x bio:', str(_be)[:120])
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
        'NHL': 'hockey/nhl', 'NFL': 'football/nfl', 'MLS': 'soccer/usa.1', 'CFL': 'football/cfl',
        'NCAAF': 'football/college-football', 'NCAAB': 'basketball/mens-college-basketball',
        'UFC': 'mma/ufc', 'EPL': 'soccer/eng.1', 'LALIGA': 'soccer/esp.1', 'UCL': 'soccer/uefa.champions'}
TIER_BADGE = {'lock': '🔒 LOCK ROOM', 'sharp': '📊 SHARP ROOM', 'whale': '🐋 WHALE ROOM',
              'free': '🆓 FREE PICK', 'challenge': '💵 CHALLENGE'}

ESPORT_LABEL = {'cs2': 'CS2', 'lol': 'LoL', 'valorant': 'VALORANT', 'dota2': 'Dota 2'}
def league_tag(sport):
    """League/game label for every pick + result line: NBA · MLB · UFC · CS2 · LoL ..."""
    s = (sport or '').lower()
    if s in ESPORT_LABEL:
        return ESPORT_LABEL[s]
    return (sport or 'PICK').upper()

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
    if p.get('prop'):
        return grade_prop_pick(p, away_s, home_s)
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
    m_sp = re.search(r'([+-]\d+(\.\d+)?)\s*$', desc)
    if m_sp and 'ml' not in desc:
        sp = float(m_sp.group(1))
        side = None
        if side_in_desc(p.get('homeTeam'), desc):
            side = 'home'
        elif side_in_desc(p.get('awayTeam'), desc):
            side = 'away'
        if not side:
            return None
        margin = (home_s - away_s) if side == 'home' else (away_s - home_s)
        cover = margin + sp
        u = float(p['units']) if p.get('units') is not None else 1.0
        if cover == 0:
            return 'PUSH', 0.0
        return ('WIN' if cover > 0 else 'LOSS'), (profit_units(p['odds'], u) if cover > 0 else -u)
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
    """Native X text post. OAuth2 (user-context) first, then every OAuth1 app we hold.
    A dead credential must never silence receipts — each failure falls through to the next path."""
    c = x_creds_load()
    last = None
    errs = []
    if c.get('oauth2_access'):
        try:
            if time.time() > c.get('oauth2_expires_at', 0):
                c = x_oauth2_refresh(c)
            body = {'text': text}
            if quote_id:
                body['quote_tweet_id'] = str(quote_id)
            req = urllib.request.Request('https://api.x.com/2/tweets',
                                         data=json.dumps(body).encode(), method='POST',
                                         headers={'Authorization': f"Bearer {c['oauth2_access']}",
                                                  'Content-Type': 'application/json', 'User-Agent': 'SHiFTPicks/1.0'})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except Exception as e:
            last = f'oauth2: {e}'
            print('x_post_native oauth2 path failed:', str(e)[:150])
    payload = {'text': text}
    if quote_id:
        payload['quote_tweet_id'] = str(quote_id)
    data = json.dumps(payload).encode()
    url = 'https://api.x.com/2/tweets'
    for name, ck, cs, at, ats in x_oauth1_sets(c):
        try:
            hdr = x_oauth1_sign('POST', url, ck, cs, at, ats)
            req = urllib.request.Request(url, data=data, method='POST',
                headers={'Authorization': hdr, 'Content-Type': 'application/json', 'User-Agent': 'SHiFTPicks/1.0'})
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
        if last:
            print('x_post_native attempt:', str(last)[:160])
            errs.append(str(last)[:160])
            last = None
    if errs:
        print('x_post_native: all native paths failed ->', ' || '.join(errs)[:300])
    return None

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
                                          'User-Agent': 'SHiFTPicks/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise Exception(f'HTTP {e.code}: {e.read()[:300]}')


def x_oauth1_sign(method, url, ck, cs, at, ats, extra=None):
    import hmac, hashlib, secrets, urllib.parse
    op = {'oauth_consumer_key': ck, 'oauth_nonce': secrets.token_hex(16),
          'oauth_signature_method': 'HMAC-SHA1', 'oauth_timestamp': str(int(time.time())),
          'oauth_version': '1.0'}
    if at:
        op['oauth_token'] = at
    if extra:
        op.update(extra)  # form/query params must join the signature base (OAuth1 spec)
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
                         'User-Agent': 'SHiFTPicks/1.0'})
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
                         'User-Agent': 'SHiFTPicks/1.0'})
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
                 'User-Agent': 'SHiFTPicks/1.0'})
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
                headers={'Authorization': hdr, 'Content-Type': 'application/json', 'User-Agent': 'SHiFTPicks/1.0'})
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

def x_update_bio(text, url=None):
    """Update the X profile bio (160 chars max) and optionally the profile website.
    Form params join the signature per OAuth1 spec."""
    import urllib.parse, urllib.request
    text = (text or '')[:160]
    if not text and not url:
        return {'error': 'empty bio'}
    sets = x_oauth1_sets(x_creds_load())
    if not sets:
        return {'error': 'no oauth1 credential set'}
    api_url = 'https://api.x.com/1.1/account/update_profile.json'
    params = {'description': text}
    if url:
        params['url'] = url
    data = urllib.parse.urlencode(params).encode()
    last = None
    for name, ck, cs, at, ats in sets:
        try:
            hdr = x_oauth1_sign('POST', api_url, ck, cs, at, ats, params)
            req = urllib.request.Request(api_url, data=data, method='POST',
                headers={'Authorization': hdr, 'Content-Type': 'application/x-www-form-urlencoded',
                         'User-Agent': 'SHiFTPicks/1.0'})
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
    print('x_post_native all paths failed:', str(last)[:200])
    return None

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
    # X POLICY: direct publishing of URL drafts is blocked (403) — schedule ~2 min out instead.
    body = {'platforms': {'x': {'enabled': True, 'posts': [{'text': text}]}},
            'publish_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() + 130))}
    req = urllib.request.Request('https://api.typefully.com/v2/social-sets/321722/drafts',
                                 data=json.dumps(body).encode(), method='POST',
                                 headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            res = json.load(r)
        did = (res or {}).get('id') or (res or {}).get('draft_id')
        return {'data': {'id': str(did), 'scheduled': True}} if did else res
    except Exception as e:
        print('x_post typefully fallback failed:', str(e)[:150])
        return None

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

def tier_ad_text(tier, days=7):
    """X ad for a positive-units tier: record, units, the $100-per-pick example, store link.
    Returns None when the tier isn't +u over the window (advertising law: only winners advertise)."""
    import datetime as _dt
    pj = gh_get_json_ref('picks.json', 'main') or {'picks': []}
    cutoff = (_dt.datetime.utcnow() - _dt.timedelta(days=days)).strftime('%Y-%m-%d')
    rows = [p for p in pj.get('picks', [])
            if p.get('tier') == tier and p.get('result') in ('WIN', 'LOSS', 'PUSH')
            and str(p.get('date', '')) >= cutoff]
    if not rows:
        return None
    w = sum(1 for p in rows if p['result'] == 'WIN')
    l = sum(1 for p in rows if p['result'] == 'LOSS')
    u = sum(units_of(p) for p in rows)
    if u <= 0:
        return None
    emo, price = {'whale': ('🐋', 99.99), 'sharp': ('📊', 49.99), 'lock': ('🔒', 29.99)}.get(tier, ('⚡', 0))
    profit = u * 100
    mult = (profit / price) if price else 0
    lines = [f"{emo} The {tier.upper()} room went {w}-{l} this week — +{u:.1f} units.",
             f"",
             f"$100 on every pick we posted = +${profit:,.0f} in 7 days."
             + (f" The room costs ${price:.0f}/mo — it paid for itself {mult:.0f}x over." if mult >= 1 else ''),
             f"",
             f"Every pick posted before start. Every result receipted.",
             f"💎 {STORE_PAGE}"]
    return ('\n'.join(lines), u)


def _x_weight(s):
    """Approximate X weighted length (emoji/CJK count 2)."""
    w = 0
    for ch in s:
        o = ord(ch)
        w += 2 if (o > 0xFFFF or 0x1F000 <= o <= 0x1FAFF or 0x2600 <= o <= 0x27BF or o >= 0x2E80) else 1
    return w


def x_receipt_text(r, all_picks=None, chal=None):
    def _fit(txt):
        # over-weight receipts shed the invite line before X rejects the whole post
        return txt if _x_weight(txt) <= 275 else txt.replace(f"\n🎁 Picks · receipts · $50 giveaway: {STORE_PAGE}", '')
    odds = r.get('odds'); odds_s = f"({odds:+d})" if isinstance(odds, int) else '(ML)'
    badge = TIER_BADGE.get(r.get('tier'), '')
    # daily-rotating param busts X's card cache so the SHiFT banner preview always renders
    store_link = f"https://thelineshift.github.io/SHiFTS/upgrade.html?utm_source=x_{time.strftime('%Y%m%d')}"

    rec_lines = []
    if all_picks is not None and r.get('tier') != 'challenge':
        tw, tl, tu = tier_season_line(all_picks, r.get('tier'))
        mname, mw, ml, mpu, mu = month_block(all_picks)
        rec_lines.append(f"{badge.split()[0]} season {tw}-{tl} ({'+' if tu >= 0 else ''}{tu:.1f}u) · 📅 SHiFT overall — all tiers, {mname}: {mw}-{ml} ({'+' if mu >= 0 else ''}{mu:.1f}u) · resets monthly")
    if r.get('tier') == 'challenge' and chal:
        rec = chal.get('record', {})
        rec_lines.append(f"💵 bankroll ${chal.get('balance', 0):.2f} ({rec.get('wins', 0)}-{rec.get('losses', 0)}) · goal $1,000")
    rec_block = ('\n' + '\n'.join(rec_lines) + '\n') if rec_lines else ''
    seed = f"{r.get('id')}{r.get('date')}{r.get('result')}"
    rtag = f"[{league_tag(r.get('sport'))}] "
    if r['result'] == 'WIN':
        base = f"🧾 RESULT {badge}: {rtag}{r['desc']} {odds_s} ✅ +{r.get('units')}u\n{r.get('score')}\n{rec_block}\n"
        # paid-room winners funnel to the store (link renders our branded preview card on X)
        if r.get('tier') in ('whale', 'sharp', 'lock'):
            return _fit(base + "💎 Every play like this, every 4 hours → " + store_link + f"\n🎁 Picks · receipts · $50 giveaway: {STORE_PAGE}")
        return _fit(base + _pick_closer(CLOSERS_WIN, seed) + f"\n🎁 Picks · receipts · $50 giveaway: {STORE_PAGE}")
    if r['result'] == 'PUSH':
        return _fit(f"🧾 RESULT {badge}: {rtag}{r['desc']} {odds_s} 🟰 PUSH — stake back.\n{r.get('score')}\n{rec_block}\n"
                + _pick_closer(CLOSERS_PUSH, seed) + f"\n🎁 Picks · receipts · $50 giveaway: {STORE_PAGE}")
    return _fit(f"🧾 RESULT {badge}: {rtag}{r['desc']} {odds_s} ❌ {r.get('units')}u\n{r.get('score')}\n{rec_block}\n"
            + _pick_closer(CLOSERS_LOSS, seed) + f"\n🎁 Picks · receipts · $50 giveaway: {STORE_PAGE}")

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
                          f"[{league_tag(p.get('sport'))}] {p.get('desc')} ({p.get('odds')}) · Final: {p.get('score')}\n"
                          f"**BALANCE: {_money_e(chal['balance'])} ${chal['balance']:.2f}** (goal: 💰 ${chal.get('goal', 1000):.0f}) · record {chal['record']['wins']}-{chal['record']['losses']}\n"
                          f"Next challenge action lands with the 4 PM ET scan. — SHiFT ⚡")
            try:
                await ch.edit(name=f"💵{int(chal.get('start',100))}-to-💰{int(chal.get('goal',1000))}-{chal['record']['wins']}-{chal['record']['losses']}")
            except Exception as _ne:
                print('challenge rename:', _ne)
    except Exception as e:
        print('settle_challenge error:', e)

@tasks.loop(seconds=1200)
async def grader():
    try:
        if not client.guilds:
            return
        try:
            await grade_parlays(client.guilds[0])
        except Exception as e:
            print('parlay grade call:', e)
        doc = await asyncio.to_thread(gh_get_json_ref, 'picks.json', 'main')
        new_results = []
        for p in (doc.get('picks') or []):
            try:
                if str(p.get('result', '')).upper() not in ('', 'PENDING', 'NONE', 'NULL'):
                    continue
                sport = (p.get('sport') or '').upper()
                # ESPORTS: settle via PandaScore past matches (receipts for cs2/lol/valorant/dota2)
                if sport.lower() in PS_GAMES:
                    team = p.get('team') or (p.get('desc', '').rsplit(' ML', 1)[0] if ' ML' in (p.get('desc') or '') else '')
                    d = await ps_settle_leg_detail(team, sport, p.get('date', ''))
                    if not d:
                        continue
                    leg = d['result']
                    u = float(p.get('units') or 1.0)
                    o = p.get('odds') if isinstance(p.get('odds'), int) else -110
                    p['odds'] = o  # legacy None-odds picks settle at standard -110 — receipts always show a number
                    p['result'] = leg.upper()
                    p['units_result'] = round(profit_units(o, u), 2) if leg == 'win' else (-u if leg == 'loss' else 0.0)
                    opp = d.get('opp') or p.get('vs') or p.get('opp') or ''
                    if opp and not p.get('vs'):
                        p['vs'] = opp  # heal the record so receipts never say "opponent" again
                    if d.get('score'):
                        p['score'] = f"{team} {d['score']} {opp} · final".strip()
                    elif opp:
                        p['score'] = f"{team} def. {opp}" if leg == 'win' else (f"{opp} def. {team}" if leg == 'loss' else f"{team} vs {opp} — draw")
                    else:
                        p['score'] = f"{team} — {leg}"
                    new_results.append(p)
                    continue
                if sport not in ESPN:
                    continue  # tennis/golf -> odds-API path
                gt = pick_game_utc(p.get('date', ''), p.get('time_et'))
                if not gt or time.time() < gt + 5400:
                    continue  # earliest a final is possible
                sb = await asyncio.to_thread(espn_fetch, sport, p['date'].replace('-', ''))
                # HEAL: picks registered without team names used to be ungradable forever
                if not p.get('awayTeam') or not p.get('homeTeam'):
                    ha, hh = heal_pick_teams(p, sb)
                    if ha and hh:
                        p['awayTeam'], p['homeTeam'] = ha, hh
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
                _tw, _tl, _tu = tier_season_line(doc.get('picks', []), p.get('tier'))
                await ch.send(f"🧾 **RESULT {badge}:** [{league_tag(p.get('sport'))}] {p.get('desc')} ({fmt_odds_num(p.get('odds')) if isinstance(p.get('odds'), int) else 'ML'}) {e} **{p['result']}** {us}\n"
                              f"Final: {p.get('score')}{overnight}\n"
                              f"📊 {str(p.get('tier') or '').upper()} season: **{_tw}-{_tl}** ({'+' if _tu >= 0 else ''}{_tu:.1f}u) — every play receipted")
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

# ---------- X ENGAGEMENT ENGINE (owner decree 2026-07-26) ----------
# Research base (2026 X algorithm + pick-seller playbooks): replies outweigh likes ~15x;
# the first 30-60 min decide distribution; bettors are active 8-10a / 12-2p / 5-7p ET;
# the mix that grows pick sellers is ~40% value / 30% conversation-starters / 30% persona+proof;
# honest loss posts build more trust than win spam. Every post funnels to the store page
# or the Discord — driving buys is the end goal. Ads keep drainer priority; these posts
# only fill the quiet air between receipts.

X_EDU_BANK = [
    "Closing line value is the only report card that matters. Beat the close consistently and you're sharp. Don't, and you're donating. ⚡",
    "A -110 coin flip needs a 52.4% hit rate to break even. Before any play, ask: does my read beat 52.4%? No answer, no bet.",
    "Units > dollars. A $50 bettor and a $5,000 bettor compare records in units — the only honest scoreboard.",
    "Parlays multiply the book's edge, not just the payout. Chained +EV legs beat the tax — chained guesses feed it.",
    "Line moved toward your play after you took it? That's the market agreeing with you. Track it for a month — it says more than your W-L.",
    "Public money inflates favorites and overs. The value lives where the timeline isn't looking.",
    "Chasing a loss doubles your ruin rate, not your comeback odds. Same formula, every play, hot or cold — that's the whole secret.",
    "Esports prices whipsaw on roster news. The edge isn't knowing who's better — it's knowing what the market hasn't priced yet.",
    "Any capper who deletes losses is selling you a highlight reel. Receipts or it didn't happen.",
    "One good day means nothing. One good month means something. One good season means everything. Play the long game.",
]

def _x_fit275(text):
    """X weighted-length fit: drop the funnel line first, then hard-trim as last resort."""
    if _x_weight(text) <= 275:
        return text
    parts = text.rsplit('\n', 1)
    if len(parts) == 2 and _x_weight(parts[0]) <= 275:
        return parts[0]
    while _x_weight(text) > 275 and len(text) > 40:
        text = text[:-2]
    return text

def x_engagement_text(st, kind, picks):
    """One SHiFT-voiced engagement post. Every number on X must be receipt-true — pull
    live from state, never invent. Returns text or None (caller falls back or skips)."""
    stats = st.get('pm_stats', {})
    w_, l_, pnl = stats.get('wins', 0), stats.get('losses', 0), stats.get('pnl', 0.0)
    doy = int(time.strftime('%j', time.gmtime()))
    if kind == 'question':
        pend = [p for p in (picks or []) if not p.get('result') and (p.get('start') or 0) > time.time()]
        if not pend:
            return None
        p = pend[0]
        desc = p.get('desc') or p.get('pick') or ''
        odds = p.get('odds')
        odds_s = f" ({odds:+d})" if isinstance(odds, int) else ''
        return _x_fit275(f"⚡ On the next card: {desc}{odds_s}\n\nTail or fade? 👇\nFull card drops in the Discord first — the free room never closes.")
    if kind == 'edu':
        return _x_fit275(f"🎓 Betting school, one minute:\n\n{X_EDU_BANK[doy % len(X_EDU_BANK)]}\n\n💎 {STORE_PAGE}")
    if kind == 'persona':
        variants = [
            "The 4 AM card went up while the timeline slept. Six cards a day, every day, holidays included. The shift never ends. ⚡",
            "Receipts don't sleep either: every result graded in public, wins AND losses. That's the difference between a record and a story.",
            "Half the edge is showing up when nobody's watching. 12a · 4a · 8a · 12p · 4p · 8p ET — the board gets run every four hours. ⚡",
            "We graded a LOSS in public and posted the autopsy with it, then fixed what caused it. That's how a desk gets sharper.",
        ]
        return _x_fit275(variants[doy % len(variants)] + f"\n\n💎 {STORE_PAGE}")
    if kind == 'proof':
        sign = '+' if pnl >= 0 else ''
        _dep = _desk_basis(st.get('pm_stats'))
        _acct = (st.get('pm_stats') or {}).get('account')
        _money = (f"💰 account ${float(_acct):.2f} · net {'+' if (float(_acct) - _dep) >= 0 else ''}${float(_acct) - _dep:.2f} on ${_dep:.2f} in"
                  if _acct else f"net {'+' if pnl >= 0 else ''}${pnl:.2f} realized on ${_dep:.2f} in")
        return _x_fit275(
            f"📈 SHiFT desk, live: {w_}-{l_} · {_money}.\n"
            f"Every entry, exit and autopsy posted in public — receipts or it didn't happen.\n"
            f"💎 {STORE_PAGE}")
    if kind == 'lesson':
        les = st.get('pm_lessons') or []
        if not les:
            return None
        l0 = les[-1]
        try:
            age = time.time() - datetime.datetime.fromisoformat(str(l0.get('ts', '')).replace('Z', '+00:00')).timestamp()
        except Exception:
            age = 999999
        if age > 72 * 3600:
            return None
        return _x_fit275(f"🔬 Desk autopsy ({l0.get('result')}): {l0.get('lesson')}\n\nWe post the misses too — receipts or it didn't happen.\n💎 {STORE_PAGE}")
    if kind == 'store-ad':
        ads = [
            f"🔒 The Lock Room is where parlays are unlocked — every parlay built off the day's cards, posted before game time.\nUp to 18 picks/day · 7-day free trial.\n💎 {STORE_PAGE}",
            f"📊 SHARP: we show where the number is wrong, why, and by how much. Fair price vs book price — gap math on the card.\nUp to 24 picks/day · 7-day free trial.\n💎 {STORE_PAGE}",
            f"🐋 WHALE: SHiFT's most confident plays dealt to you first — house law, every card. Props, POD & parlays first. Live injury/delay wire. Weekly deep-dive.\n💎 {STORE_PAGE}",
            f"🆓 The free room eats too: a daily free pick, $50 in SOL drawn every Sunday, every result receipted in public.\nCome see a real record.\n💎 {STORE_PAGE}",
            f"🖥️ What SHiFT watches every scan: NFL · NBA · MLB · NHL · UFC · NCAAF · NCAAB · WNBA · CFL · EPL · La Liga · UCL · UECL · UEL · MLS · NWSL · LATAM + Nordic soccer · ATP + WTA tennis (Elo model) · PGA\n+ CS2 · LoL · Dota 2 · Valorant · Overwatch esports.\n💎 {STORE_PAGE}",
        ]
        return _x_fit275(ads[doy % len(ads)])
    return None

@tasks.loop(seconds=900)
async def x_engagement_watch():
    """3 conversation-starting posts/day at ET peak windows (9a/1p/6p ET = 13/17/22 UTC).
    Rotating kinds per weekday so the same hour never carries the same flavor twice.
    Shares the global X pacing with receipts — one post at a time, ≥40 min apart."""
    try:
        now = time.gmtime()
        windows = {13: ('question', 'persona'), 17: ('edu', 'lesson'), 22: ('store-ad', 'proof')}
        if now.tm_hour not in windows or now.tm_min > 14:
            return
        st = await asyncio.to_thread(get_state)
        if not st:
            return
        day = time.strftime('%Y-%m-%d', now)
        key = f'{day}-{now.tm_hour}'
        log = st.setdefault('x_eng_log', {})
        if log.get(key):
            return
        if time.time() - float(st.get('last_x_receipt_ts') or 0) < 40 * 60:
            return  # global X pacing — receipts and ads own the air first
        kinds = windows[now.tm_hour]
        kind = kinds[int(now.tm_wday) % 2]
        picks_doc = await asyncio.to_thread(gh_get_json_ref, 'picks.json', 'main') or {}
        picks = picks_doc.get('picks') or []
        text = x_engagement_text(st, kind, picks)
        if not text and kind != kinds[0]:
            kind = kinds[0]
            text = x_engagement_text(st, kind, picks)
        if not text:
            return
        resp = await asyncio.to_thread(x_post, text, None)
        if resp is None:
            print('[engage] post failed — retries next cycle')
            return
        log[key] = kind
        for k0 in list(log.keys()):
            if k0 < day:
                del log[k0]
        st['last_x_receipt_ts'] = time.time()
        await asyncio.to_thread(gh_put, 'bot_state.json', st, f'x engagement: {kind}')
        print(f'[engage] posted {kind}')
    except Exception as e:
        print('x_engagement_watch error:', e)

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
        # ADS FIRST — owner decree 2026-07-25: advertising always has priority over results posts
        ads = state.get('x_ads') or []
        if ads:
            resp = await asyncio.to_thread(x_post, ads[0], None)
            if resp is None:
                print('x_drainer: ad post failed — keeping ad queued')
                return
            state['x_ads'] = ads[1:]
            state['last_x_receipt_ts'] = time.time()
            await asyncio.to_thread(gh_put, 'bot_state.json', state, 'x ad fired')
            print('[drainer] ad posted')
            return
        r = queue[0]
        # WINNERS-ONLY LAW (owner decree 2026-07-27): "all losers to not be posted to X,
        # only discord — only winning bets should be posted on X." Non-WIN results drain
        # silently (no X post, no pacing stamp) — Discord receipts carry every result.
        if r.get('result') != 'WIN':
            state['unannounced_results'] = queue[1:]
            await asyncio.to_thread(gh_put, 'bot_state.json', state, f"x receipt skipped (non-WIN {r.get('result')}): {r.get('id')}")
            print(f"x_drainer: skipped {r.get('id')} ({r.get('result')}) — losers stay on Discord, per WINNERS-ONLY LAW")
            return
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
        mname, mw, ml, mpu, mu = month_block(all_picks)
        lines.append(f"**FULL BOARD: {tot_w}-{tot_l}" + (f"-{tot_p}" if tot_p else "") + f" ({'+' if tot_u >= 0 else ''}{tot_u:.2f}u).**")
        lines.append(f"🗓️ **SHiFT OVERALL — ALL TIERS, {mname.upper()}: {mw}-{ml}" + (f"-{mpu}" if mpu else "")
                     + f" ({'+' if mu >= 0 else ''}{mu:.2f}u)** — our one record across every room this month; resets the 1st")
        lines.append(f"📅 2026 season to date: {sw}-{sl}" + (f"-{sp}" if sp else "") + f" ({'+' if su >= 0 else ''}{su:.2f}u)")
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
        # WINNERS-ONLY LAW (owner decree 2026-07-27): X gets winning results ONLY.
        # The full board recap posts on GREEN days (units > 0), win-framed, no loss
        # column. The full record — every W and L — stays on Discord receipts.
        if tot_u > 0:
            try:
                _season = f"\n📅 2026 season: {'+' if su >= 0 else ''}{su:.1f}u" if su > 0 else ''
                xt = (f"✅ FULL BOARD — green day {mmdd}\n\n"
                      f"🏆 {tot_w} winner{'s' if tot_w != 1 else ''} · {'+'}{tot_u:.1f}u banked{_season}\n\n"
                      f"Every pick posted early, every result graded in public. First month FREE 👆")
                await asyncio.to_thread(x_post, xt)
            except Exception as e:
                print('recap X error:', e)
        else:
            print(f"[recap] X recap skipped (red day {tot_u:+.1f}u) — Discord receipts carry the full board, per WINNERS-ONLY LAW")
        print('recap posted for', recap_date)
    except Exception as e:
        print('recap_watch error:', e)

def month_block(all_picks):
    """Owner decree 7/26: the advertised 'overall record' = SHiFT's record across ALL tiers
    for the current ET month — it resets on the 1st of each month. Say so every time."""
    _et_now = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=4)
    ym, mname = _et_now.strftime('%Y-%m'), _et_now.strftime('%B')
    ps = [p for p in all_picks if p.get('result') in ('WIN', 'LOSS', 'PUSH')
          and p.get('tier') in ('lock', 'sharp', 'whale', 'free')
          and str(p.get('date', '')).startswith(ym)]
    w = sum(1 for p in ps if p['result'] == 'WIN')
    l = sum(1 for p in ps if p['result'] == 'LOSS')
    pu_ = sum(1 for p in ps if p['result'] == 'PUSH')
    u = sum(units_of(p) for p in ps)
    return mname, w, l, pu_, u

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

def perf_report(tier, days, title):
    """W-L-P, units, ROI, by-sport split, best/worst read for one tier over the last N days."""
    import datetime as _dt
    pj = gh_get_json_ref('picks.json', 'main') or {'picks': []}
    cutoff = (_dt.datetime.utcnow() - _dt.timedelta(days=days)).strftime('%Y-%m-%d')
    rows = [p for p in pj.get('picks', [])
            if p.get('tier') == tier and p.get('result') in ('WIN', 'LOSS', 'PUSH')
            and str(p.get('date', '')) >= cutoff]
    if not rows:
        return f"**{title}**\nNo settled plays in the window — the machine only fires when the edge is real."
    w = sum(1 for p in rows if p['result'] == 'WIN')
    l = sum(1 for p in rows if p['result'] == 'LOSS')
    pu = sum(1 for p in rows if p['result'] == 'PUSH')
    u = sum(units_of(p) for p in rows)
    risk = sum(float(p.get('units') or 1) for p in rows if p['result'] != 'PUSH')
    roi = (u / risk * 100) if risk else 0.0
    rec = f"{w}-{l}" + (f"-{pu}" if pu else '')
    by_sport = {}
    for p in rows:
        s = by_sport.setdefault(str(p.get('sport') or '?').upper(), [0, 0, 0.0])
        if p['result'] == 'WIN':
            s[0] += 1
        elif p['result'] == 'LOSS':
            s[1] += 1
        s[2] += units_of(p)
    sport_line = ' · '.join(f"{k} {v[0]}-{v[1]} ({v[2]:+.1f}u)"
                           for k, v in sorted(by_sport.items(), key=lambda kv: kv[1][2], reverse=True))
    best = max(rows, key=lambda p: units_of(p))
    worst = min(rows, key=lambda p: units_of(p))
    return (f"**{title}**\n"
            f"Record: **{rec}** · Units: **{u:+.1f}u** · ROI: **{roi:+.1f}%**\n"
            f"By sport: {sport_line}\n"
            f"🏆 Best read: {best.get('desc') or best.get('pick') or '?'} ({units_of(best):+.1f}u)\n"
            f"🩸 Worst read: {worst.get('desc') or worst.get('pick') or '?'} ({units_of(worst):+.1f}u)")


async def weekly_analytics_report(g0, st):
    """Sunday 10 AM ET — the weekly analytics the teaser promises: per-tier breakdown in each paid
    room + the public tier-by-tier board in #weekly-analytics. Truth-in-advertising law."""
    try:
        for tier in ('whale', 'sharp', 'lock'):
            rm = find_channel(g0, SCAN_ROOMS[tier])
            if rm:
                _body = await asyncio.to_thread(perf_report, tier, 7, f"📊 WEEKLY ANALYTICS — {tier.upper()} ROOM (last 7 days)")
                await rm.send(_body + "\n\nEvery play receipted on the public ledger. Next report next Sunday 10 AM ET. ⚡")
                await asyncio.sleep(1)
        pub = find_channel(g0, 'weekly-analytics')
        if pub:
            parts = [await asyncio.to_thread(perf_report, 'free', 7, '🆓 FREE BOARD (last 7 days)')]
            for tier, emo in (('whale', '🐋'), ('sharp', '📊'), ('lock', '🔒')):
                parts.append(await asyncio.to_thread(perf_report, tier, 7, f'{emo} {tier.upper()} (last 7 days)'))
            _pj = await asyncio.to_thread(gh_get_json_ref, 'picks.json', 'main') or {'picks': []}
            mname, mw, ml, mpu, mu = month_block(_pj.get('picks', []))
            await pub.send(("\n\n".join(parts))[:1700]
                           + f"\n\n🗓️ **SHiFT OVERALL — ALL TIERS, {mname.upper()}: {mw}-{ml}" + (f"-{mpu}" if mpu else "")
                           + f" ({'+' if mu >= 0 else ''}{mu:.2f}u)** — one record across every room this month; resets the 1st."
                           + "\n🔒 Full breakdowns live in each tier room. The ledger never hides a week.")
        st.setdefault('scan_events', {})['weekly-report'] = time.strftime('%Y-%m-%d')
    except Exception as e:
        print('weekly_analytics_report error:', e)


async def monthly_deep_dive(g0, st, days=7):
    """WHALE WEEKLY DEEP-DIVE (owner decree 2026-07-25: was monthly) — autopsy of the window
    + the desk's own lessons, plus the tier-by-tier board in #monthly-deepdive."""
    try:
        body = await asyncio.to_thread(perf_report, 'whale', days, f'🐋 WEEKLY DEEP-DIVE — WHALE MASTERCLASS (last {days} days)')
        lessons = (st.get('pm_lessons') or [])[-3:]
        if lessons:
            body += '\n\n🧠 **Desk autopsy — the lessons the machine wrote this month:**'
            for ls in lessons:
                body += f"\n• {(ls.get('lesson') if isinstance(ls, dict) else str(ls))[:220]}"
        body += "\n\nNext month's attack plan: same law — edge or no bet. The desk keeps scanning 24/7."
        rm = find_channel(g0, SCAN_ROOMS['whale'])
        if rm:
            await rm.send(body[:1950])
        dd = find_channel(g0, 'monthly-deepdive')
        if dd:
            parts = []
            for t, e in (('whale', '🐋'), ('sharp', '📊'), ('lock', '🔒'), ('free', '🆓')):
                parts.append(await asyncio.to_thread(perf_report, t, days, f'{e} {t.upper()} — {days} days'))
            await dd.send(("\n\n".join(parts))[:1950]
                          + "\n\n🐋 The full masterclass lives in the Whale room.")
        st.setdefault('scan_events', {})['monthly-report'] = time.strftime('%Y-%m-%d')
    except Exception as e:
        print('monthly_deep_dive error:', e)


# ============================ 24h CLAIM LAW (owner decree 2026-07-27) ============================
# Every drawn winner has 24 hours to respond in #giveaway. No response -> that prize
# redraws from the remaining tickets, new 24h clock, old winner out for this draw.
GW_CLAIM_FILE = 'giveaway_draw.json'
GW_LADDER = [('runner-up', 15), ('grand', 35)]  # sunset pricing (captain's order 7/24): $50 = $35/$15

def _gw_pool(conf):
    """Weighted ticket pool from the confirmed ledger. One ticket-set per Discord account
    (same discord_id under two X handles = one person — first confirm stands)."""
    entries, seen_did = [], set()
    for hk, rec in sorted((conf or {}).items(), key=lambda kv: (kv[1] or {}).get('ts', '')):
        did = (rec or {}).get('discord_id') or ''
        if did and did in seen_did:
            continue
        if did:
            seen_did.add(did)
        entries.append({'hk': hk, 'handle': (rec or {}).get('handle') or hk,
                        'discord': (rec or {}).get('discord') or '',
                        'discord_id': did, 'mult': max(1, int((rec or {}).get('mult') or 1))})
    pool = []
    for e in entries:
        pool += [e['hk']] * e['mult']
    return pool, entries

def _gw_deadline(w, gd):
    ts = w.get('drawn_at') or gd.get('drawn_at') or ''
    try:
        import datetime as _dt2
        t0 = _dt2.datetime.fromisoformat(str(ts).replace('Z', '+00:00')).timestamp()
    except Exception:
        t0 = time.time()
    return t0 + float(gd.get('claim_hours', 24)) * 3600

def _gw_ment(rec):
    return f"<@{rec['discord_id']}>" if rec.get('discord_id') else f"@{rec.get('handle', '?')}"

async def _gw_save_draw(gd, msg):
    await asyncio.to_thread(gh_put, GW_CLAIM_FILE, gd, msg, QUEUE_BRANCH)

async def run_giveaway_draw(g0):
    """Weekly $50 draw ($35 grand / $15 runner-up) — weighted random from the confirmed
    ledger, X-steps verified per winner (the $0 alternative to X Basic), then the 24h
    CLAIM LAW clocks start. Writes giveaway_draw.json so the claim watch enforces redraws."""
    import random as _rnd
    gch = find_channel(g0, 'giveaway') if g0 else None
    conf = await asyncio.to_thread(gh_get_json_ref, 'giveaway_confirmed.json', QUEUE_BRANCH)
    if not conf:
        if gch:
            await gch.send("🎁 Draw time — but the ledger is empty. No entries this week.")
        return
    st = await asyncio.to_thread(get_state)
    pool, entries = _gw_pool(conf)
    by_hk = {e['hk']: e for e in entries}
    if len(entries) < 2:
        if gch:
            await gch.send("🎁 Draw time — but fewer than 2 unique entrants. Pot rolls to next Sunday.")
        return
    now_s = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    gd = {'draw_id': time.strftime('%Y-%m-%d', time.gmtime()),
          'drawn_at': now_s, 'claim_hours': 24, 'round': 1,
          'pool_size': len(pool), 'entrants': len(entries),
          'ladder': {'grand': 35, 'runner-up': 15}, 'winners': [], 'history': []}
    taken = set()
    for rank, prize in GW_LADDER:
        rec = None
        for _att in range(4):
            hk = _rnd.choice([t for t in pool if by_hk[t]['handle'] not in taken] or pool)
            cand = by_hk.get(hk) or {}
            handle = cand.get('handle') or hk
            followed = liked = reposted = None
            try:
                followed, liked, reposted = await _gw_live_checks(handle, st)
            except Exception:
                pass
            if followed is False or liked is False or reposted is False:
                if gch:
                    await gch.send(f"🎁 Drawn entry @{handle} is missing steps — **redrawing**…")
                pool = [t for t in pool if t != hk]
                if not pool:
                    break
                continue
            rec = {'rank': rank, 'prize': prize, 'handle': handle, 'hk': hk,
                   'discord': cand.get('discord') or '', 'discord_id': cand.get('discord_id') or '',
                   'status': 'pending', 'drawn_at': now_s, 'claimed_at': None,
                   'verified': bool(followed and liked and reposted)}
            break
        if rec is None:
            continue
        taken.add(rec['handle'])
        gd['winners'].append(rec)
        await asyncio.sleep(2 if rank == 'runner-up' else 0)  # drumroll beat: runner-up first
        if gch:
            vline = "✅ Steps verified live." if rec['verified'] else "👀 X steps get eyeballed before payout (reads degraded)."
            medal = '🥈' if rank == 'runner-up' else '🥇'
            await gch.send(f"{medal} **{rank.upper()} — ${prize} SOL: {_gw_ment(rec)} (@{rec['handle']})** 🎉\n"
                           f"{vline} {cand.get('mult', 1)}x ticket(s) in a pool of {gd['pool_size']}.\n"
                           f"⏰ **Claim within 24 hours — reply right here.** No response = this prize redraws. ⚡")
        await _gw_dm_winner(rec)  # INSTANT PAYOUT LAW: auto-DM the moment they're drawn
    await _gw_save_draw(gd, 'giveaway draw ' + gd['draw_id'])

async def giveaway_claim_check_message(message):
    """24h CLAIM LAW: a pending winner speaking in #giveaway claims their prize.
    Returns True when the message WAS a claim (caller stops processing)."""
    gd = await asyncio.to_thread(gh_get_json_ref, GW_CLAIM_FILE, QUEUE_BRANCH)
    if not gd:
        return False
    aid, aname = str(message.author.id), str(message.author)
    hit = None
    for w in gd.get('winners') or []:
        if w.get('status') != 'pending':
            continue
        if (w.get('discord_id') and w['discord_id'] == aid) or (w.get('discord') and w['discord'] == aname):
            hit = w
            break
    if hit is None:
        return False
    hit['status'] = 'claimed'
    hit['claimed_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    await _gw_save_draw(gd, 'giveaway claim @' + hit.get('handle', '?'))
    if await _gw_try_payout(message, hit, gd):  # pasted the address right here — pay instantly
        return True
    await _gw_dm_winner(hit)  # auto-DM: address goes in DMs, prize ships on receipt
    medal = '🥇' if hit.get('rank') == 'grand' else '🥈'
    await message.channel.send(f"{message.author.mention} {medal} **CLAIMED — ${hit.get('prize')} SOL is yours.** "
                               f"Check your DMs — send your SOL address there and you're paid **immediately**. ⚡")
    return True

@tasks.loop(seconds=300)
async def giveaway_claim_watch():
    """Enforces the 24h CLAIM LAW: expired pending winners get redrawn automatically."""
    try:
        if not client.guilds:
            return
        gd = await asyncio.to_thread(gh_get_json_ref, GW_CLAIM_FILE, QUEUE_BRANCH)
        if not gd:
            return
        pending = [w for w in (gd.get('winners') or []) if w.get('status') == 'pending']
        # INSTANT PAYOUT LAW: every pending winner gets the auto-DM (covers winners drawn
        # before this law shipped — they get DM'd on this tick) — then the 24h check.
        dm_changed = False
        for w in pending:
            if not w.get('dm_sent'):
                await _gw_dm_winner(w)
                dm_changed = True
        if dm_changed:
            await _gw_save_draw(gd, 'winner DMs sent')
        if not pending:
            return
        now = time.time()
        expired = [w for w in pending if now > _gw_deadline(w, gd)]
        if not expired:
            return
        g0 = client.guilds[0]
        gch = find_channel(g0, 'giveaway')
        conf = await asyncio.to_thread(gh_get_json_ref, 'giveaway_confirmed.json', QUEUE_BRANCH) or {}
        pool, entries = _gw_pool(conf)
        by_hk = {e['hk']: e for e in entries}
        # anyone who has already won (any round, any status) or was voided sits out
        out = {w.get('handle') for w in (gd.get('winners') or []) + (gd.get('history') or [])}
        out |= {v.get('handle') for v in (gd.get('voided') or [])}
        import random as _rnd
        now_s = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        changed = False
        for w in expired:
            cands = [t for t in pool if by_hk.get(t, {}).get('handle') not in out]
            if not cands:
                if gch:
                    await gch.send(f"🎁 ${w.get('prize')} prize: nobody left to redraw — the captain handles this one manually.")
                w['status'] = 'expired'
                gd.setdefault('history', []).append(w)
                gd['winners'] = [x for x in gd['winners'] if x is not w]
                changed = True
                continue
            hk = _rnd.choice(cands)
            cand = by_hk[hk]
            gd.setdefault('history', []).append({**w, 'status': 'expired'})
            gd['winners'] = [x for x in gd['winners'] if x is not w]
            nw = {'rank': w.get('rank'), 'prize': w.get('prize'), 'handle': cand['handle'], 'hk': hk,
                  'discord': cand.get('discord') or '', 'discord_id': cand.get('discord_id') or '',
                  'status': 'pending', 'drawn_at': now_s, 'claimed_at': None, 'redraw_of': w.get('handle')}
            gd['winners'].append(nw)
            gd['round'] = int(gd.get('round', 1)) + 1
            out.add(cand['handle'])
            changed = True
            if gch:
                medal = '🥇' if nw.get('rank') == 'grand' else '🥈'
                await gch.send(f"⏰ 24 hours, no response from @{w.get('handle')} — the ${nw.get('prize')} prize **redraws**…\n"
                               f"{medal} **NEW {str(nw.get('rank')).upper()} WINNER: {_gw_ment(nw)} (@{nw['handle']})** 🎉\n"
                               f"Same house rule: **24 hours to reply right here** or it redraws again. ⚡")
            await _gw_dm_winner(nw)  # INSTANT PAYOUT LAW: auto-DM redrawn winners too
        if changed:
            await _gw_save_draw(gd, 'giveaway redraw round ' + str(gd.get('round')))
    except Exception as e:
        print('giveaway_claim_watch error:', e)

# ============================ INSTANT PAYOUT LAW (owner decree 2026-07-27) ============================
# Winners are auto-DMed the moment they're drawn; when they send a SOL address, the
# prize ships on-chain IMMEDIATELY from the GIVEAWAY wallet (never the ops wallet —
# 7/27: ops holds 0.00 SOL, the prize pot lives in the giveaway wallet).
GW_SOL_RE = re.compile(r'\b([1-9A-HJ-NP-Za-km-z]{32,44})\b')

async def _gw_dm_winner(w):
    """Auto-DM a drawn winner: prize, reply-with-address instructions, 24h clock."""
    did = w.get('discord_id')
    if not did:
        return False
    try:
        u = await client.fetch_user(int(did))
        medal = '🥇' if w.get('rank') == 'grand' else '🥈'
        await u.send(f"{medal} **YOU WON ${w.get('prize')} in SOL — SHiFT's giveaway!** 🎉\n"
                     f"Reply **right here in this DM** with your Solana address and the prize ships on-chain **immediately**.\n"
                     f"⏰ The 24-hour clock runs from the draw — no response anywhere means the prize redraws. ⚡")
        w['dm_sent'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        return True
    except Exception as e:
        w['dm_sent'] = 'blocked'
        print('gw dm:', str(e)[:100])
        return False

async def _gw_pay_prize(prize_usd, addr):
    """USD-denominated prize -> SOL at spot, sent from the giveaway wallet.
    Returns (sig, sol_amt, px, None) or (None, None, None, err)."""
    try:
        from solders.keypair import Keypair
        from solders.pubkey import Pubkey
        from solders.system_program import transfer, TransferParams
        from solders.message import Message as SMsg
        from solders.transaction import Transaction
        from solders.hash import Hash as SHash
        dest = Pubkey.from_string(str(addr).strip())  # raises on a bad address
    except Exception:
        return None, None, None, 'that doesn\'t look like a valid Solana address — double-check and resend'
    def _rpc(method, params):
        body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode()
        req = urllib.request.Request('https://solana-rpc.publicnode.com', data=body,
                                     headers={'Content-Type': 'application/json', 'User-Agent': 'shift-ops'})
        return json.loads(urllib.request.urlopen(req, timeout=20).read())
    try:
        pxr = await asyncio.to_thread(_http_json, 'https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd')
        px = float(pxr['solana']['usd'])
        if px <= 0:
            raise ValueError('bad price')
    except Exception as e:
        return None, None, None, 'price feed hiccup: ' + str(e)[:80]
    sol_amt = round(float(prize_usd) / px, 4)
    if sol_amt <= 0 or sol_amt > 50:
        return None, None, None, 'amount out of bounds'
    try:
        sec = await asyncio.to_thread(gh_get_json_ref, 'wallets_secret.json', QUEUE_BRANCH)
        kp = Keypair.from_bytes(bytes.fromhex(sec['solana_giveaway']['secret_hex']))
        bal = await asyncio.to_thread(_rpc, 'getBalance', [str(kp.pubkey())])
        lam = int(bal['result']['value'])
        need = int(sol_amt * 1_000_000_000) + 10000
        if lam < need:
            return None, None, None, f'prize wallet is short right now ({lam / 1e9:.4f} SOL on hand) — the captain has been pinged, your prize is locked in'
        bh = await asyncio.to_thread(_rpc, 'getLatestBlockhash', [{'commitment': 'finalized'}])
        blockhash = SHash.from_string(bh['result']['value']['blockhash'])
        ix = transfer(TransferParams(from_pubkey=kp.pubkey(), to_pubkey=dest, lamports=int(sol_amt * 1_000_000_000)))
        tx = Transaction([kp], SMsg([ix], kp.pubkey()), blockhash)
        import base64 as _b64
        sig = await asyncio.to_thread(_rpc, 'sendTransaction', [_b64.b64encode(bytes(tx)).decode(), {'encoding': 'base64'}])
        return sig['result'], sol_amt, px, None
    except Exception as e:
        return None, None, None, str(e)[:160]

async def _gw_try_payout(message, w, gd):
    """Winner sent a SOL address -> pay immediately. True = handled; False = no address found."""
    m = GW_SOL_RE.search(message.content or '')
    if not m:
        return False
    if w.get('status') == 'paid':
        await message.channel.send("Already paid — check your wallet (tx is in your DMs). ⚡")
        return True
    if w.get('status') == 'paying':
        await message.channel.send("Already processing your payout — give it a minute. ⏳")
        return True
    prev = w.get('status') or 'pending'
    w['status'] = 'paying'  # payout lock: exactly one in-flight send per prize, ever
    await _gw_save_draw(gd, 'payout lock @' + w.get('handle', '?'))
    sig, amt, px, err = await _gw_pay_prize(w.get('prize'), m.group(1))
    if err:
        w['status'] = prev
        await _gw_save_draw(gd, 'payout failed @' + w.get('handle', '?'))
        await message.channel.send(f"❌ Payout hiccup: {err}\nYour prize stays locked in — try again in a few minutes. ⚡")
        return True
    now_s = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    w.update(status='paid', paid_tx=sig, sol_amount=amt, sol_price=px,
             paid_at=now_s, paid_addr=m.group(1), claimed_at=w.get('claimed_at') or now_s)
    await _gw_save_draw(gd, f"PAID ${w.get('prize')} @{w.get('handle', '?')}")
    await message.channel.send(f"✅ **PAID — {amt} SOL** (${w.get('prize')} at ${px:,.0f}/SOL)\ntx: https://solscan.io/tx/{sig} ⚡")
    try:
        g0 = client.guilds[0] if client.guilds else None
        gch = find_channel(g0, 'giveaway') if g0 else None
        lab = find_channel(g0, 'shift-lab') if g0 else None
        if gch and getattr(message.channel, 'id', None) != gch.id:
            await gch.send(f"💸 {_gw_ment(w)} (@{w.get('handle')}) has been **PAID** — ${w.get('prize')} prize shipped on-chain. ⚡")
        if lab:
            await lab.send(f"💸 giveaway payout: ${w.get('prize')} -> {amt} SOL (px {px}) to `{m.group(1)}` (@{w.get('handle')}) tx {sig}")
    except Exception as e:
        print('payout announce:', e)
    return True


def _whale_teams_in_play(picks):
    """(eid, sport, [team names]) for every unsettled pick — the games we have action on."""
    out = {}
    for p in picks:
        if p.get('result'):
            continue
        eid = p.get('eid')
        if not eid or not p.get('sport'):
            continue
        e = out.setdefault(eid, {'sport': p['sport'], 'teams': set(), 'taken': p.get('odds'), 'pick': p.get('pick')})
        for t in (p.get('home'), p.get('away')):
            if t:
                e['teams'].add(str(t))
    return out


async def _whale_injury_news(sport, teams):
    """Fresh injury/report items for our teams from the ESPN injuries endpoint."""
    path = SE_SPORTS.get(sport)
    if not path or '/' not in path:
        return []
    base = path.split('/')[0]
    league = path.split('/')[1]
    items = []
    try:
        d = await asyncio.to_thread(se_get, f'https://site.api.espn.com/apis/site/v2/sports/{base}/{league}/injuries')
        for blk in d.get('injuries') or []:
            tname = ((blk.get('team') or {}).get('displayName')) or ''
            if not any(t and (t in tname or tname in t) for t in teams):
                continue
            for it in (blk.get('injuries') or [])[:4]:
                ath = (it.get('athlete') or {}).get('displayName') or 'Player'
                st_ = it.get('status') or ''
                det = it.get('shortComment') or it.get('longComment') or ''
                items.append(f"🚑 **{tname}:** {ath} — **{st_}**. {det[:160]}")
    except Exception:
        pass
    return items


@tasks.loop(seconds=1800)
async def whale_intel():
    """WHALE LIVE WIRE (owner decree 2026-07-25): injuries, postponements/delays, and line moves
    on games we have action on — posted to the Whale lounge as they happen, never on a schedule."""
    try:
        if not client.guilds:
            return
        g0 = client.guilds[0]
        rm = find_channel(g0, SCAN_ROOMS['whale'])
        if not rm:
            return
        st = await asyncio.to_thread(get_state)
        seen = st.setdefault('whale_intel_seen', {})
        now = time.time()
        for k in [k for k, v in seen.items() if now - float(v) > 3 * 86400]:
            seen.pop(k, None)
        picks = (await asyncio.to_thread(gh_get_json_ref, 'picks.json', 'main')).get('picks', [])
        games = _whale_teams_in_play(picks)
        items = []
        all_teams = set()
        for e in games.values():
            all_teams |= e['teams']
        sports = {e['sport'] for e in games.values()}
        for sp in sports:
            for it in await _whale_injury_news(sp, all_teams):
                items.append((sp, it))
        # delays / postponements + line moves per game
        for eid, e in list(games.items())[:14]:
            path = SE_SPORTS.get(e['sport'])
            if not path or '/' not in path:
                continue
            base, league = path.split('/')[0], path.split('/')[1]
            try:
                d = await asyncio.to_thread(se_get, f'https://site.api.espn.com/apis/site/v2/sports/{base}/{league}/summary?event={eid}')
            except Exception:
                continue
            try:
                stt = ((d.get('header') or {}).get('competitions') or [{}])[0].get('status') or {}
                stn = ((stt.get('type') or {}).get('name') or '')
                if any(x in stn for x in ('POSTPONED', 'DELAYED', 'SUSPENDED', 'CANCELED')):
                    detail = (stt.get('type') or {}).get('detail') or stn.replace('STATUS_', '').title()
                    items.append((e['sport'], f"⏱️ **GAME STATUS — {e.get('pick', eid)}:** **{detail}**. Plan your positions accordingly."))
                pc = (d.get('pickcenter') or [{}])[0]
                cur = (((pc.get('odds') or {}).get('homeTeamOdds') or {}).get('moneyLine'))
                if isinstance(cur, (int, float)) and isinstance(e.get('taken'), (int, float)) and abs(cur - e['taken']) >= 25:
                    items.append((e['sport'], f"📉 **LINE MOVE — {e.get('pick', '')}:** we dealt **{e['taken']:+d}**, board now **{int(cur):+d}** — {'we beat the close ✅' if (cur - e['taken']) * (1 if e['taken'] < 0 else -1) < 0 else 'market moved against the number'}."))
            except Exception:
                continue
        posted = 0
        for sp, it in items:
            key = f"{sp}:{it[:48]}"
            if key in seen:
                continue
            seen[key] = now
            try:
                await rm.send("🐋 **LIVE WIRE** — " + it[:900])
                posted += 1
                await asyncio.sleep(1)
            except Exception:
                pass
            if posted >= 5:
                break
        if posted or seen:
            await asyncio.to_thread(gh_put, 'bot_state.json', st, 'whale intel')
    except Exception as e:
        print('whale_intel error:', e)


@tasks.loop(seconds=3600)
async def teaser_watch():
    try:
        if not client.guilds:
            return
        guild = client.guilds[0]
        now = time.gmtime()
        import datetime as _dt
        today = _dt.date(now.tm_year, now.tm_mon, now.tm_mday)
        state = await asyncio.to_thread(get_state)
        tz = state.setdefault('teasers', {})
        # ---- REPORT FIRE GATES (truth-in-advertising: the teasers promise these exact times)
        _dirty = False
        if now.tm_wday == 6 and now.tm_hour == 14 and tz.get('weekly_fired') != today.isoformat():
            tz['weekly_fired'] = today.isoformat()
            _dirty = True
            await weekly_analytics_report(guild, state)
        # DRAW GATE, miss-proofed (7/26 lesson: an hourly tick == 22 dies silently to any
        # outage/restart — the whole show vanished with zero trace). Fires on the FIRST
        # tick at/after Sunday 22:00 UTC, plus a Monday catch-up if Sunday was lost.
        # giveaway_draw.json's draw_id is the satisfaction marker — a draw that already
        # ran (any path, manual included) never double-fires. Stamp un-sets on failure
        # so the next tick retries: never a silent skip again.
        last_sun = today - _dt.timedelta(days=(today.weekday() + 1) % 7)
        sun_key = last_sun.isoformat()
        _gd_due = (now.tm_wday == 6 and now.tm_hour >= 22) or ((today - last_sun).days == 1)
        if _gd_due and tz.get('draw_fired') != sun_key:
            _gdoc = await asyncio.to_thread(gh_get_json_ref, 'giveaway_draw.json', QUEUE_BRANCH) or {}
            if _gdoc.get('draw_id') != sun_key:
                tz['draw_fired'] = sun_key
                _dirty = True
                try:
                    await run_giveaway_draw(guild)  # Sunday 6 PM ET — winner-only verification
                except Exception as _gde:
                    tz.pop('draw_fired', None)
                    print('giveaway draw retry next tick:', _gde)
        if now.tm_hour == 13 and tz.get('x_ad') != today.isoformat():
            tz['x_ad'] = today.isoformat()
            _dirty = True
            try:
                _best = None
                for _t in ('whale', 'sharp', 'lock'):
                    _r = await asyncio.to_thread(tier_ad_text, _t, 7)
                    if _r and (_best is None or _r[1] > _best[1]):
                        _best = (_r[0], _r[1], _t)
                if _best:
                    state.setdefault('x_ads', []).append(_best[0])
                    print('[teaser] x ad queued for', _best[2])
            except Exception as _ae:
                print('x ad build:', _ae)
        # RETIRED v9.21.8: old 16:00 UTC deep-dive gate double-fired alongside the new
        # weekly_deepdive_watch engine (Saturday 20:00 UTC, full autopsy + charts). One engine only.
        if now.tm_hour != 12:
            if _dirty:
                await asyncio.to_thread(gh_put, 'bot_state.json', state, 'report fired')
            return
        next_sun = today + _dt.timedelta(days=(6 - today.weekday()) % 7)
        ws = next_sun.isoformat()
        if tz.get('weekly') != ws:
            ch = find_channel(guild, 'weekly-analytics')
            if ch:
                await ch.send(f"📊 **WEEKLY ANALYTICS — next report: Sunday {next_sun.strftime('%b %d')}, 10:00 AM ET**\nFull-board review: tier-by-tier records, units chart, best/worst reads of the week, and what changes next week. 🎯")
                tz['weekly'] = ws
        sat = today + _dt.timedelta(days=(5 - today.weekday()) % 7)
        ws2 = sat.isoformat()
        if tz.get('weekly-dd') != ws2:
            ch = find_channel(guild, 'monthly-deepdive')
            if ch:
                await ch.send(f"🐋 **WHALE WEEKLY DEEP-DIVE — next report: Saturday {sat.strftime('%b %d')}, 12:00 PM ET**\nWhale-tier masterclass: the week's full autopsy, where the edge came from, bankroll math, and next week's attack plan.")
                tz['weekly-dd'] = ws2
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
        # DEPLOY ≠ CRASH: a fresh Railway deployment id means this boot is a clean deploy,
        # not a restart loop — reset the storm counter. Only same-deployment restarts
        # (real crash loops) accumulate toward the circuit breaker.
        dep_id = os.environ.get('RAILWAY_DEPLOYMENT_ID', '')
        if dep_id and st.get('last_deploy_id') != dep_id:
            st['last_deploy_id'] = dep_id
            st['boot_log'] = []
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
PS_GAMES = {'cs2': 'csgo', 'lol': 'lol', 'dota2': 'dota2', 'valorant': 'valorant', 'ow': 'ow'}
SCAN_ROOMS = {'free': 'free-pick', 'lock': 'lock-room', 'sharp': 'sharp-room', 'whale': 'whale-room'}
# room identity colors (embed rail): instant visual tier recognition, zero confusion
TIER_COLORS = {'whale': 0xF5C518, 'sharp': 0x3498DB, 'lock': 0x2ECC71, 'free': 0x95A5A6}

def se_get(url, timeout=12):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

# All leagues the engine checks every scan. Offseason leagues return empty -> the pool
# naturally fills with whatever is live (esports carries slow days per CROSS-SPORT LAW).
SE_SPORTS = {'mlb': 'baseball/mlb', 'wnba': 'basketball/wnba', 'mls': 'soccer/usa.1',
             'nfl': 'football/nfl', 'ncaaf': 'football/college-football', 'cfl': 'football/cfl',
             'nba': 'basketball/nba', 'ncaab': 'basketball/mens-college-basketball',
             'nhl': 'hockey/nhl', 'ufc': 'mma/ufc',
             'epl': 'soccer/eng.1', 'laliga': 'soccer/esp.1', 'ucl': 'soccer/uefa.champions',
             # ALL-MARKETS LAW (7/28): every ESPN-carried league the exchange lists gets a
             # model read. All endpoints probe-verified 200 with live events (7/28).
             'uecl': 'soccer/uefa.europa.conf', 'uel': 'soccer/uefa.europa',
             'arg1': 'soccer/arg.1', 'bra1': 'soccer/bra.1', 'bra2': 'soccer/bra.2',
             'swe1': 'soccer/swe.1', 'nor1': 'soccer/nor.1', 'nwsl': 'soccer/usa.nwsl',
             'ecu1': 'soccer/ecu.1', 'col1': 'soccer/col.1',
             'sudam': 'soccer/conmebol.sudamericana', 'libert': 'soccer/conmebol.libertadores'}
# Schedule-aware sports: ESPN carries the tournament shell only (no matchups/odds).
# Golf stays shell-aware (outrights need full-field odds — no free source); tennis broke
# out of this tier on 7/28 via the TennisAbstract Elo model (se_tennis_elo/pm_tennis_prob).
SE_AWARE = {'golf': 'golf/pga/scoreboard', 'atp': 'tennis/atp/scoreboard', 'wta': 'tennis/wta/scoreboard'}
SE_HOME_ADV = {'mlb': 0.030, 'wnba': 0.045, 'mls': 0.045, 'nfl': 0.020, 'ncaaf': 0.030, 'cfl': 0.025,
               'nba': 0.030, 'ncaab': 0.035, 'nhl': 0.025, 'ufc': 0.0,
               'epl': 0.040, 'laliga': 0.040, 'ucl': 0.030,
               'uecl': 0.030, 'uel': 0.030, 'sudam': 0.030, 'libert': 0.030,
               'arg1': 0.040, 'bra1': 0.040, 'bra2': 0.040, 'swe1': 0.040, 'nor1': 0.040,
               'nwsl': 0.040, 'ecu1': 0.040, 'col1': 0.040}

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
                out.append({'sport': sport, 'start': e['date'], 'eid': e.get('id'),
                            'home': home['team']['displayName'], 'away': away['team']['displayName'],
                            'recs': recs, 'ml_home': mh, 'ml_away': ma,
                            'total': odds.get('overUnder'), 'spread': odds.get('spread')})
            except Exception:
                continue
    return out

def se_edges(g, now_ts, hours=4, min_edge=0.06):
    """Turn one game into edge candidates. Edge = OUR probability (records + home/away
    splits via log5 + home advantage) minus the book's no-vig implied probability.
    We only fire where our number beats theirs by >= 6 points."""
    ok, t = _in_window(g['start'], now_ts, hours)
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
        # ODDS DISCIPLINE LAW (ALL sports, owner decree — 9.7.3 form confirmed final):
        # straights are near-even or better (>= -150). -150..-400 with a real edge =
        # PARLAY MATERIAL, never a straight. Worse than -400 = dead to us entirely.
        if ml is None or p_imp is None or ml < -400 or ml > 200:
            continue
        edge = p_ours - p_imp
        if edge < min_edge:
            continue
        split = (g['recs'].get(side) or {}).get('home' if side == 'home' else 'road', '')
        split_s = f", {split} {'at home' if side == 'home' else 'on the road'}" if split else ''
        out.append({'sport': g['sport'], 'pick': f"{team} ML", 'vs': opp, 'odds': ml,
                    'units': 1.5 if edge >= 0.12 else 1.0, 'edge': edge, 'start': t,
                    'market': 'ML', 'prob': p_ours, 'team': team, 'opp': opp, 'side': side,
                    'reserve': ml < -150, 'eid': g.get('eid'),
                    'analysis': f"{(g['recs'].get(side) or {}).get('total','?')} overall{split_s} — "
                                f"our {p_ours:.0%} vs book {p_imp:.0%} (no-vig)"})
        # MARKET VARIETY LAW: juiced MLs become run-line/spread plays at the standard number
        if ml < -150 and g.get('spread') is not None:
            sp = g['spread'] if side == 'home' else -g['spread']
            if -15.0 <= sp <= -0.5:
                out.append({'sport': g['sport'], 'pick': f"{team} {sp:+.1f}", 'vs': opp, 'odds': -110,
                            'units': 1.0, 'edge': edge, 'start': t, 'variety': True,
                            'market': 'run line' if g['sport'] in ('mlb', 'nhl') else 'spread',
                            'prob': p_ours, 'team': team, 'opp': opp, 'side': side, 'eid': g.get('eid'),
                            'analysis': f"{(g['recs'].get(side) or {}).get('total','?')} overall{split_s} — "
                                        f"ML juiced to {ml:+d}, so the {sp:+.1f} at the standard number is the bet; model makes {team} {p_ours:.0%} straight up"})
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


def _whale_deep_read(p, rank):
    """WHALE MASTERCLASS per-play analysis (v9.22.0): the old version stapled the same
    'pricing gap' boilerplate to every play. Now: 2-3 varied angle modules per play,
    deterministic per pick (stable card, no two plays read alike). Angles: pricing gap,
    market/public money, CLV framing, matchup mechanics, form, schedule spot, unit sizing,
    and the honest 'what kills it' — because Whales pay for the full picture."""
    import hashlib as _hl
    an = (p.get('analysis') or '').split(' | ')[0].strip()
    edge_pct = round(p.get('edge', 0) * 100)
    prob = p.get('prob', 0) or 0
    prob_s, book_s = f"{prob:.0%}", f"{(prob - p.get('edge', 0)):.0%}"
    ml = p.get('odds') if isinstance(p.get('odds'), int) else None
    imp_s = (f"{(100 / (ml + 100)) if ml > 0 else (-ml / (-ml + 100)):.0%}" if ml else None)
    team = p['pick'][:-3] if p['pick'].endswith(' ML') else p['pick']
    sport = p.get('sport') or ''
    esp = sport in ('cs2', 'lol', 'dota2', 'valorant')
    h = int(_hl.md5((p['pick'] + (p.get('vs') or '') + str(p.get('start'))).encode()).hexdigest(), 16)

    def m_gap():
        return [
            f"Our number is **{prob_s}** against the book's **{book_s}** — a **{edge_pct}-point pricing gap**, and that gap is the entire bet: we're not predicting, we're buying the line cheaper than it's worth",
            f"The book hung **{book_s}**; SHiFT makes it **{prob_s}**. The **{edge_pct} points** in between are what we're purchasing — same game, better price",
            f"**{edge_pct} points** of daylight between fair (**{prob_s}**) and the board (**{book_s}**). Books pay out mistakes slowly — this is how we collect ours",
        ][h % 3]

    def m_public():
        return [
            "The public money is on the other side of this one — books shade toward the crowd, and the shade they built in is exactly what we're collecting",
            "This is a quiet fade-the-crowd spot: casual volume pushed the price off fair, and we're on the side the house had to sweeten",
            "Square money sees the name; sharper money sees the number. The number is the play here",
        ][(h // 3) % 3]

    def m_clv():
        if not imp_s:
            return None
        return [
            f"At {ml:+d} we need this **{imp_s}** of the time to break even — our rate says **{prob_s}**. That spread between required and expected is where bankroll grows",
            f"The ask at this price is **{imp_s}**; the expectation is **{prob_s}**. Every tick between those two numbers is long-run profit, whether or not tonight cooperates",
        ][(h // 9) % 2]

    def m_matchup():
        if esp:
            return [
                f"Strip the logos and this is a form-versus-form mismatch — recent map win rates, opener duels and late-round conversion all lean {team}, and Bo3 structure lets the better side actually prove it",
                f"The head-to-head mechanics favor {team}: stronger opening-duel conversion and a deeper map pool, which matters double in a series where the weaker side has to win twice",
            ][(h // 27) % 2]
        if sport == 'mlb':
            return [
                "The run environment tilts this one — lineup depth against the opposing arm's contact profile, plus the pen situation behind them, all grade our side's way",
                "This is a lineup-versus-arm mismatch: our side's on-base profile attacks exactly what this pitcher gives up, and the bullpen gap behind the starters widens it late",
            ][(h // 54) % 2]
        if sport in ('mls', 'epl', 'soccer', 'ucl'):
            return [
                "Chance quality beats chance volume here — our side creates cleaner looks per attack and concedes the low-value shots, which is exactly the profile moneyline prices underrate",
                "The tactical matchup favors us: their buildup stalls against this press shape, and the transition game runs through our side's strongest channel",
            ][(h // 108) % 2]
        if sport == 'ufc':
            return [
                "Styles make this fight: the grappling-and-pressure profile on our side attacks the exact defensive holes the other man has shown, over more minutes",
                "The tape says our side wins the minutes that matter — control time, damage differential, and the cardio to keep both late",
            ][(h // 216) % 2]
        return None

    def m_spot():
        return [
            "The schedule spot matters here — rest, travel and opponent workload all lean one way, and it's priced like a neutral slate",
            "Context check: this isn't a stand-alone game, it's a spot — and the spot (rest / rhythm / stakes) favors our side more than the number admits",
        ][(h // 7) % 2]

    def m_units():
        u = p.get('units', 1)
        if u >= 1.5:
            return [
                f"**{u}u says conviction** — this graded as one of the window's top positions, and the size is the opinion",
                f"The **{u}u** tag isn't decoration: graded against the full slate, this cleared our conviction bar with room",
            ][(h // 5) % 2]
        return [
            f"**{u}u — sized for the variance**, not the confidence. The edge is real; so is the coin-flip tax at this price, and the unit count is the respect",
            f"Kept at **{u}u** because the price is thin — right side, honest size. Bankroll math beats chest-beating",
        ][(h // 20) % 2]

    def m_wrong():
        if esp:
            return [
                "What kills it: early-map snowball — lose the opener duels and the form edge never gets to speak. That's the risk we're paid to hold",
                "What kills it: veto luck and one hot hand. If their star goes nuclear early, form won't save us — priced-in risk, accepted",
            ][(h // 11) % 2]
        if sport == 'mlb':
            return [
                "What kills it: one crooked inning. Baseball compresses edges into single swings — the math needs volume, and we have the volume",
                "What kills it: the bullpen door. If this becomes a reliever game early, the handicap resets — that's the variance the price pays us to carry",
            ][(h // 22) % 2]
        return [
            "What kills it: the counterpunch. If the game state flips early and our side has to chase, the edge thins fast — risk noted, priced, taken",
            "What kills it: late-game chaos. One bounce undoes 90 minutes of right — that's why the number, not the narrative, made this bet",
        ][(h // 44) % 2]

    headers = [
        "🧠 **SHiFT's read:**", "🐋 **The Whale read:**", "📖 **The case:**",
        "🔬 **Inside the pick:**", "🎯 **Why here, why now:**",
    ]
    pools = [m_gap, m_public, m_matchup, m_spot, m_clv]
    picks = []
    for i in range(len(pools)):
        mod = pools[(h + i) % len(pools)]()
        if mod:
            picks.append(mod)
        if len(picks) >= 2 + (h % 2):  # 2-3 modules
            break
    body = '.\n'.join(s.rstrip('.') for s in picks) + '.'
    if h % 3 != 1:
        body += f"\n⚠️ {m_wrong().rstrip('.')}."
    head = headers[(h // 13) % len(headers)]
    lead = f"{an}." if an else ''
    return f"{head} {lead}\n{body}\n{m_units()}"

async def scan_engine_run(g0, slot_key, dry):
    """Build + post the slot card. dry=True -> shift-lab only, no state writes."""
    lab = find_channel(g0, 'shift-lab')
    gen = lab if dry else find_channel(g0, 'general-chat')
    if not g0 or (not dry and not gen) or not lab:
        raise RuntimeError('channels not ready — client still booting; retry law will re-fire')
    tag = '[DRY RUN] ' if dry else ''
    now_ts = time.time()
    await gen.send(f"🛰️ {tag}**SCAN INITIATED — {(datetime.datetime.utcfromtimestamp(now_ts) - datetime.timedelta(hours=4)).strftime('%I %p ET')}**" if not dry else
                   f"🛰️ {tag}scan would initiate for slot {slot_key}")
    # ---- pull candidates in the 4h window: ALL leagues + esports, edge-priced
    def _et_date(ts):
        return time.strftime('%Y%m%d', time.gmtime(ts - 4 * 3600))
    games = []
    for _d in sorted({_et_date(now_ts - 600), _et_date(now_ts + 4 * 3600), _et_date(now_ts + 8 * 3600), _et_date(now_ts + 24 * 3600)}):
        games += await asyncio.to_thread(se_espn_all, _d)
    cands = []
    pulled = {}
    for g in games:
        pulled[g['sport']] = pulled.get(g['sport'], 0) + 1
        cands += se_edges(g, now_ts)
    esp = []
    for gg in ('cs2', 'lol', 'valorant', 'dota2', 'ow'):
        esp += await asyncio.to_thread(se_ps_upcoming, gg)
    # LIVE BOOK LINES (OddsPapi): real Pinnacle moneylines for esports, budget-guarded
    op_state = await asyncio.to_thread(get_state) or {}
    op_touched = False
    op_lines = {}
    try:
        def _esp_notable(m):
            ok, _t = _in_window(m['start'] or '', now_ts)
            return ok and any(k in (m['league'] or '') for k in SCAN_NOTABLE)
        for tt in {m['sport'] for m in esp if _esp_notable(m)}:
            if tt in OP_SPORT:
                ln = await op_title_lines(tt, op_state)
                if ln:
                    op_lines[tt] = ln
                    op_touched = True
    except Exception as e:
        print('op esports fetch:', e)
    async def _esp_cands(hours=4, notable=True, min_edge=0.2, need_form=True, max_pf=0.80, use_book=True):
        """Esports edge candidates. Strict pass = all bars; FILL LAW passes relax them stepwise."""
        out, ct = [], 0
        for m in esp:
            ok, t = _in_window(m['start'] or '', now_ts, hours)
            if not ok or ct >= 10:
                continue
            if notable and not any(k in (m['league'] or '') for k in SCAN_NOTABLE):
                continue
            f1 = await asyncio.to_thread(se_ps_form, m['t1']['id'], m['sport'])
            f2 = await asyncio.to_thread(se_ps_form, m['t2']['id'], m['sport'])
            ct += 1
            if not f1 or not f2 or (need_form and (f1['w'] + f1['l'] < 3 or f2['w'] + f2['l'] < 3)):
                continue
            n1, n2 = f1['w'] + f1['l'], f2['w'] + f2['l']
            w1 = f1['w'] / n1 if n1 else 0.5
            w2 = f2['w'] / n2 if n2 else 0.5
            edge = w1 - w2
            if abs(edge) < min_edge:
                continue
            fav, dog = (m['t1'], m['t2']) if edge > 0 else (m['t2'], m['t1'])
            fav_f, dog_f = (f1, f2) if edge > 0 else (f2, f1)
            league_s = (m['league'] or '').split(' 20')[0][:22]
            fw = w1 if edge > 0 else w2
            dw = w2 if edge > 0 else w1
            p_f = fw * (1 - dw) / (fw * (1 - dw) + dw * (1 - fw)) if (fw or dw) else 0.5
            p_f = min(0.90, max(0.15, p_f))
            if use_book:
                book = op_match(op_lines.get(m['sport'], []), fav['name'], dog['name'])
                if book:
                    dec_f, dec_d = book
                    vig_f, vig_d = 1 / dec_f, 1 / dec_d
                    p_book = vig_f / (vig_f + vig_d)
                    edge_b = p_f - p_book
                    if edge_b < 0.06:
                        continue
                    ml_b = dec_to_ml(dec_f)
                    if ml_b is None or ml_b > 200 or ml_b < -400:
                        continue
                    out.append({'sport': m['sport'], 'pick': f"{fav['name']} ML", 'vs': dog['name'], 'odds': ml_b,
                                'units': 1.5 if edge_b >= 0.12 else 1.0, 'edge': edge_b, 'start': t,
                                'market': f"{league_s} Bo{m['bo'] or '?'}", 'prob': p_f,
                                'team': fav['name'], 'opp': dog['name'], 'side': None,
                                'reserve': ml_b < -150,
                                'analysis': se_form_text(fav_f, dog_f, fav['name']) + f" — live Pinnacle line {ml_b:+d} (our {p_f:.0%} vs book {p_book:.0%})"})
                    continue
            if p_f > max_pf:
                continue  # past the dead zone — never a pick even in fill mode (9.7.3 law holds)
            ml_f = -round((100 * p_f / (1 - p_f)) / 5) * 5 if p_f >= 0.5 else round((100 * (1 - p_f) / p_f) / 5) * 5
            out.append({'sport': m['sport'], 'pick': f"{fav['name']} ML", 'vs': dog['name'], 'odds': ml_f,
                        'units': 1.5 if abs(edge) >= 0.4 else 1.0, 'edge': abs(edge), 'start': t,
                        'market': f"{league_s} Bo{m['bo'] or '?'}", 'prob': p_f,
                        'team': fav['name'], 'opp': dog['name'], 'side': None,
                        'reserve': p_f > 0.60,
                        'analysis': se_form_text(fav_f, dog_f, fav['name']) + f" — model line {ml_f:+d} (our {p_f:.0%})"})
        return out
    cands += await _esp_cands()
    if op_touched:
        try:
            await asyncio.to_thread(gh_put, 'bot_state.json', op_state, 'oddsPapi meta/budget')
        except Exception as e:
            print('op state save:', e)
    cands.sort(key=lambda c: -c['edge'])
    # never re-pick a game already on today's board (cross-slot dedupe)
    try:
        today_d = slot_key[:4] + '-' + slot_key[4:6] + '-' + slot_key[6:8]
        pj_have = await asyncio.to_thread(gh_get_json_ref, 'picks.json', 'main') or {}
        have_descs = {p.get('desc') for p in (pj_have.get('picks') or []) if p.get('date') == today_d}
        cands = [c for c in cands if c['pick'] not in have_descs]
    except Exception as e:
        print('cross-slot dedupe:', e)
    # ---- MARKET VARIETY LAW (owner decree 2026-07-25): never a wall of moneylines —
    # totals outliers join the spread conversions, ≤2 variety plays per card
    tots = [g for g in games if isinstance(g.get('total'), (int, float))]
    if len(tots) >= 4:
        avg = sum(g['total'] for g in tots) / len(tots)
        TH = {'mlb': 1.5, 'nhl': 1.0, 'nba': 8.0, 'nfl': 5.0, 'wnba': 6.0, 'mls': 0.5, 'epl': 0.5, 'ucl': 0.5}
        for g in tots:
            th = TH.get(g['sport'], 1.5)
            dev = g['total'] - avg
            if abs(dev) < th:
                continue
            ok, t = _in_window(g['start'], now_ts, 24)
            if not ok:
                continue
            side = 'Under' if dev > 0 else 'Over'
            edge = min(0.10, abs(dev) / max(1.0, avg) / 2)
            angle = ("slate-high number and public over money inflates these — the under is the sharp side"
                     if dev > 0 else
                     "slate-low number — priced like a dud, but this slate's bats and arms say it clears")
            cands.append({'sport': g['sport'], 'pick': f"{side} {g['total']}", 'vs': f"{g['away']} @ {g['home']}",
                          'odds': -110, 'units': 1.0, 'edge': edge, 'start': t, 'variety': True,
                          'market': 'total', 'prob': 0.5 + edge, 'team': g['home'], 'opp': g['away'],
                          'side': None, 'eid': g.get('eid'),
                          'analysis': f"posted {g['total']} vs {avg:.1f} slate average — {angle}"})
    # ---- PLAYER PROPS (owner decree 2026-07-25): 1-2 props per card when the feed has edge.
    # Metered free tier — props pull only on the 4pm/8pm ET cards, never on dry runs.
    try:
        _slot_h = int(slot_key[9:11]) if re.match(r'\d{8}-\d{2}', slot_key) else -1
        if not dry and _slot_h in ODDS_PROP_SLOTS_UTC:
            _st_odds = await asyncio.to_thread(get_state) or {}
            _oa = _odds_budget(_st_odds)
            _props, _oa = await asyncio.to_thread(odds_mlb_props, games, now_ts, _oa, _slot_h)
            if _props:
                cands += _props
                print(f"props feed: {len(_props)} prop candidates (meter {_oa.get('used')}/{ODDS_API_CAP})")
            if _oa != (_st_odds.get('odds_api') or {}):
                _st_odds['odds_api'] = _oa
                await asyncio.to_thread(gh_put, 'bot_state.json', _st_odds, 'odds-api meter')
    except Exception as e:
        print('props feed:', e)
    var = sorted([c for c in cands if c.get('variety')], key=lambda x: -x['edge'])
    _pr = [c for c in var if c.get('prop')][:2]        # up to 2 props
    _ot = [c for c in var if not c.get('prop')][:2]    # up to 2 spread/total variety
    _keep = {id(c) for c in _pr + _ot}
    cands = [c for c in cands if not c.get('variety') or id(c) in _keep]
    # ---- ODDS DISCIPLINE: juiced esports faves leave the straight pool, feed parlays
    reserves = [c for c in cands if c.get('reserve')]
    cands = [c for c in cands if not c.get('reserve')]
    # ---- FILL LAW (owner decree 2026-07-25): every tier's quota fills EVERY scan.
    # Bend OUR bars progressively — never invent games, never past the -400 dead zone.
    QUOTA = (('whale', 6), ('sharp', 4), ('lock', 3), ('free', 1))
    NEED = sum(q for _, q in QUOTA)
    _pool_keys = lambda pool: {(c['pick'], c.get('vs')) for c in pool}
    def _pool_dedupe(pool):
        seen, out = set(), []
        for c in sorted(pool, key=lambda x: -x['edge']):
            k = (c['pick'], c.get('vs'))
            if k in seen:
                continue
            seen.add(k); out.append(c)
        return out
    cands = _pool_dedupe(cands)
    fill_steps = []
    if len(cands) < NEED:
        extra = [c for g in games for c in se_edges(g, now_ts, hours=8) if not c.get('reserve')]
        if extra:
            cands = _pool_dedupe(cands + extra); fill_steps.append('+8h window')
    if len(cands) < NEED:
        extra = [c for g in games for c in se_edges(g, now_ts, hours=8, min_edge=0.03) if not c.get('reserve')]
        if extra:
            cands = _pool_dedupe(cands + extra); fill_steps.append('edge bar 3%')
    if len(cands) < NEED:
        extra = [c for c in await _esp_cands(hours=8, notable=False, min_edge=0.12, need_form=False,
                                             max_pf=0.85, use_book=False) if not c.get('reserve')]
        if extra:
            cands = _pool_dedupe(cands + extra); fill_steps.append('esports relaxed')
    if len(cands) < NEED and reserves:
        cands = _pool_dedupe(cands + reserves); fill_steps.append('juiced faves straightened')
    if len(cands) < NEED:
        extra = [c for g in games for c in se_edges(g, now_ts, hours=24, min_edge=0.03) if not c.get('reserve')]
        if extra:
            cands = _pool_dedupe(cands + extra); fill_steps.append('+24h slate')
    if len(cands) < NEED:
        extra = [c for c in await _esp_cands(hours=24, notable=False, min_edge=0.08, need_form=False,
                                             max_pf=0.85, use_book=False) if not c.get('reserve')]
        if extra:
            cands = _pool_dedupe(cands + extra); fill_steps.append('esports +24h')
    if len(cands) < NEED:
        # FLOOR LIFT (terminal backstop): any modeled favorite on the slate — quota is law
        extra = [c for g in games for c in se_edges(g, now_ts, hours=24, min_edge=0.0) if not c.get('reserve')]
        if extra:
            cands = _pool_dedupe(cands + extra); fill_steps.append('floor lift')
    if fill_steps:
        print('FILL LAW bent:', ', '.join(fill_steps), f'-> pool {len(cands)}')
    # ---- deal tiers (whale-first). PICKS LAW: whale 6 / sharp 4 / lock 3 / free 1 per scan
    deal, _i = {}, 0
    for _t, _q in QUOTA:
        deal[_t] = cands[_i:_i + _q]; _i += _q
    # ---- PARLAY: deep slates deal one 2-3 leg parlay, rotated across the paid rooms
    parlay_built = None
    if len(cands) + len(reserves) >= 6:
        try:
            st_rot = await asyncio.to_thread(get_state) or {}
            parlay_built, p_room = se_build_parlay(cands[NEED:] + [r for r in reserves if (r['pick'], r.get('vs')) not in _pool_keys(cands)], st_rot.get('parlay_rot', 0))
            if parlay_built:
                # PARLAYS UNLOCKED LAW (owner audit 7/26): every paid room gets every parlay.
                # Rotation starved Lock (0 parlays in a week) while its page sells 'parlays unlocked'.
                for _pt in ('whale', 'sharp', 'lock'):
                    deal[_pt].append(dict(parlay_built))
                if not dry:  # dry sims never touch rotation state
                    st_rot['parlay_rot'] = st_rot.get('parlay_rot', 0) + 1
                    await asyncio.to_thread(gh_put, 'bot_state.json', st_rot, 'parlay rotation')
        except Exception as e:
            print('parlay build:', e)
    slate_s = ' · '.join(f"{k.upper()} {v}" for k, v in sorted(pulled.items()) if v)
    aware = await asyncio.to_thread(se_aware_live, time.strftime('%Y%m%d', time.gmtime()))
    if aware:
        slate_s += (' · ' if slate_s else '') + 'on radar: ' + ', '.join(aware)
    picks_out = []
    _etn = datetime.datetime.utcfromtimestamp(now_ts) - datetime.timedelta(hours=4)
    # DATE-STAMPED SIGNATURE LAW (v9.21.7): hour-only sigs self-deduped against yesterday's
    # same-hour card inside the 20-message room-history window — cards silently vanished.
    slot_et = f"{_etn.strftime('%a')} {_etn.month}/{_etn.day} · {_etn.strftime('%I %p ET').lstrip('0')}"
    def _nxt_et():
        h = int(time.strftime('%H', time.gmtime(now_ts)))
        nxt = next((s for s in SCAN_SLOTS_UTC if s > h), 0)
        et = (nxt - 4) % 24
        return f"{et % 12 or 12} {'AM' if et < 12 else 'PM'} ET"
    if not cands:
        # FEED CATASTROPHE: full ladder ran and nothing exists. NEVER SILENT LAW — every room hears it.
        alert = (f"⚠️ {tag}**FEED ALERT — {slot_et}**\n\nEvery source came back empty this window "
                 f"(checked {slate_s or 'all leagues'} + esports, full fill ladder run). No games = no card — "
                 f"the only excuse allowed. Full card next scan **{_nxt_et()}**. ⚡")
        if not dry:
            await gen.send(alert)
            for _t2, _ch2 in SCAN_ROOMS.items():
                _r2 = find_channel(g0, _ch2)
                if _r2:
                    try:
                        await _r2.send(embed=discord.Embed(description=alert, color=TIER_COLORS[_t2]))
                    except Exception:
                        pass
        else:
            await gen.send(f"{tag}would post feed-alert resolution (checked: {slate_s})")
        return
    def _why(p, rank):
        team = p['pick'][:-3] if p['pick'].endswith(' ML') else p['pick']
        edge_pct = round(p['edge'] * 100)
        an = p.get('analysis', '')
        prob = p.get('prob')
        prob_s = f"{prob:.0%}" if isinstance(prob, float) else None
        imp_s = None
        if isinstance(p.get('odds'), int) and p['odds']:
            ml = p['odds']
            imp_s = f"{(100 / (ml + 100)) if ml > 0 else (-ml / (-ml + 100)):.0%}"
        import hashlib
        h = int(hashlib.md5((p['pick'] + (p.get('vs') or '')).encode()).hexdigest(), 16)
        if p['sport'] in ('cs2', 'lol', 'dota2', 'valorant'):
            tmpls = [
                f"🧠 **The read:** {an}. That's a **{edge_pct}-point** gap over {p['vs']} the number hasn't priced.",
                f"📈 **Market angle:** {an}. Books hang respect on the wrong side — **{edge_pct} points** of model edge, taken.",
                f"⚔️ **Matchup read:** {an}. When the form gap runs **{edge_pct} points** wide, the better side wins this spot more than the price says.",
                f"🔥 **Momentum play:** {an}. Ride the **{edge_pct}-point** form edge before the market catches up.",
            ]
        else:
            value = (f"Model makes {team} **{prob_s}** — books imply **{imp_s}**."
                     if (prob_s and imp_s) else f"Model finds **{edge_pct} points** of value here.")
            tmpls = [
                f"🧠 **The read:** {an}. {value} SHiFT presses the gap.",
                f"📈 **Market angle:** {an}. The number's off by **{edge_pct} points** — this is where CLV hunters eat.",
                f"⚔️ **Matchup read:** {an}. {team} in the better spot and priced like it isn't — **{edge_pct}-point** edge.",
                f"💰 **Value hunt:** {an}. The book shaded this line toward the public side; we collect the **{edge_pct} points** they left.",
                f"📊 **Form + price:** {an}. {value} Small edge, real edge, pressed.",
            ]
        return tmpls[h % len(tmpls)]
    rank_of = {id(p): i for i, p in enumerate(cands)}
    upg_ch = find_channel(g0, 'upgrade')
    upg_ment = f'<#{upg_ch.id}>' if upg_ch else '#upgrade'
    for tier in ('whale', 'sharp', 'lock', 'free'):
        plays = deal[tier]
        room = lab if dry else find_channel(g0, SCAN_ROOMS[tier])
        if not room:
            continue
        emo = {'whale': '🐋', 'sharp': '📊', 'lock': '🔒', 'free': '🎯'}[tier]
        if not plays:
            if tier == 'free':
                paid = sum(len(deal[t]) for t in ('lock', 'sharp', 'whale'))
                extra = f" The paid rooms took {paid} plays — the best free-qualified edge just didn't clear our bar." if paid else ''
                gw_ch2 = find_channel(g0, 'giveaway')
                gw_ment2 = f"<#{gw_ch2.id}>" if gw_ch2 else 'the giveaway room'
                if not await _room_already_posted(room, f"**FREE PICK — {slot_et}**"):
                    await room.send(embed=discord.Embed(description=f"🎯 {tag}**FREE PICK — {slot_et}**\n\nNo free play this window — nothing met our edge bar, and we don't force bets.{extra} Next scan **{_nxt_et()}**.\n💎 [Every edge, every 4 hours — unlock the paid rooms](https://thelineshift.github.io/SHiFTS/upgrade.html?utm_source=discord_free) → {upg_ment}\n🎁 Sunday 6 PM ET — $50 SOL draw in {gw_ment2} ⚡", color=TIER_COLORS['free']))
                else:
                    print(f'[scan] dedupe@room: free {slot_et} empty-note already posted')
            else:
                # NEVER SILENT LAW: paid rooms always hear something — quota shortfall is said out loud
                if not await _room_already_posted(room, f"**{tier.upper()} ROOM — {slot_et}**"):
                    await room.send(embed=discord.Embed(description=f"{emo} {tag}**{tier.upper()} ROOM — {slot_et}**\n\nThe slate ran dry even after the fill ladder — only {len(cands)} playable edges existed this window, and the bigger rooms got dealt first. Full quota next scan **{_nxt_et()}** — a short room never stands. ⚡", color=TIER_COLORS[tier]))
                else:
                    print(f'[scan] dedupe@room: {tier} {slot_et} dry-note already posted')
            continue
        lines = []
        for n, p in enumerate(plays, 1):
            odds_s = f" ({fmt_odds_num(p['odds'])})" if p['odds'] is not None else ''
            if p.get('parlay'):
                leg_lines = '\n'.join(f"   • [{league_tag(lg.get('sport'))}] {lg['pick']} ({fmt_odds_num(lg['odds'])}) vs {lg['vs']}" for lg in p['legs'])
                lines.append(f"{n}\u20e3 🎰 **{p['pick']}{odds_s} — {p['units']}u**\n{leg_lines}\n"
                             f"{p['market']} · first leg {_et(p['start'])}"
                             + (f"\n🧠 **Why it's the play:** every leg cleared our edge bar — combined model probability {p.get('prob', 0):.0%} vs the price's implied {(1 / (ml_to_dec(p['odds']) or 2)):.0%}. That's value stacked on value." if tier != 'free' else ''))
            else:
                if tier == 'whale':
                    why_deep = _whale_deep_read(p, rank_of.get(id(p), n))
                    extras = se_whale_extras(p)
                    lines.append(f"{n}\u20e3 **{p['pick']}{odds_s}** vs {p['vs']} — {p['units']}u\n"
                                 f"{league_tag(p.get('sport'))} · {p['market']} · {_et(p['start'])}\n"
                                 f"📊 {p.get('analysis','')}\n"
                                 f"{why_deep}" + (f"\n💰 **The number:** {the_number(p)}" if the_number(p) else "")
                                 + (f"\n{extras}" if extras else ''))
                elif tier == 'sharp':
                    lines.append(f"{n}\u20e3 **{p['pick']}{odds_s}** vs {p['vs']} — {p['units']}u\n"
                                 f"{league_tag(p.get('sport'))} · {p['market']} · {_et(p['start'])}\n"
                                 f"📊 {p.get('analysis','')}\n"
                                 f"{_why(p, rank_of.get(id(p), n))}"
                                 + (f"\n💰 **The number:** {the_number(p)}" if the_number(p) else ""))
                elif tier == 'lock':
                    lines.append(f"{n}\u20e3 **{p['pick']}{odds_s}** vs {p['vs']} — {p['units']}u\n"
                                 f"{league_tag(p.get('sport'))} · {p['market']} · {_et(p['start'])}\n"
                                 f"📊 {p.get('analysis','')}\n"
                                 f"{_why(p, rank_of.get(id(p), n))}")
                else:
                    # free is the funnel — bare pick, no sauce (owner decree 2026-07-25)
                    lines.append(f"{n}\u20e3 **{p['pick']}{odds_s}** vs {p['vs']} — {p['units']}u\n"
                                 f"{league_tag(p.get('sport'))} · {p['market']} · {_et(p['start'])}\n")
            reg = {'id': f"{p['pick'].lower().replace(' ', '-')[:28]}-{slot_key[4:8]}" + (f'-{tier}' if p['sport'] == 'parlay' else ''), 'date': slot_key[:4] + '-' + slot_key[4:6] + '-' + slot_key[6:8],
                   'sport': p['sport'], 'desc': p.get('picks_desc') or p['pick'], 'market': p['market'], 'odds': p['odds'],
                   'units': p['units'], 'tier': tier, 'time_et': _et(p['start']),
                   'vs': p.get('vs'), 'team': p.get('team'), 'opp': p.get('opp'),
                   'analysis': (p.get('analysis', '') + ' | ' + ('parlay — every leg cleared the edge bar' if p.get('parlay') else _why(p, rank_of.get(id(p), n)).replace('🧠 **Why it\'s the play:** ', '')))[:300]}
            if isinstance(p.get('odds'), (int, float)) and 0 < abs(p['odds']) < 100:
                print('ODDS GUARD: skipping corrupt line', p.get('pick'), p.get('odds'))
                continue
            if p.get('prop'):
                reg['prop'] = p['prop']
                if p.get('eid'):
                    reg['eid'] = p['eid']
            if not p.get('parlay') and p.get('team') and p.get('opp'):
                # grading needs both sides named — never register a nameless pick again
                if p.get('side') == 'home':
                    reg['homeTeam'], reg['awayTeam'] = p['team'], p['opp']
                else:
                    reg['awayTeam'], reg['homeTeam'] = p['team'], p['opp']
            if p.get('parlay'):
                reg['legs'] = [{'team': lg['pick'][:-3] if lg['pick'].endswith(' ML') else lg['pick'],
                                'vs': lg['vs'], 'sport': lg['sport'],
                                'date': time.strftime('%Y-%m-%d', time.gmtime(lg['start'] - 4 * 3600)),
                                'time_et': _et(lg['start'])} for lg in p['legs']]
            picks_out.append(reg)
        body = f"{emo} {tag}**{tier.upper()} ROOM — {slot_et} CARD** ({len(plays)} play{'s' if len(plays) > 1 else ''})\n\n" + '\n\n'.join(lines)
        if tier == 'free':
            cnts = ' · '.join(f"{e} +{len(deal[t])}" for t, e in (('lock', '🔒'), ('sharp', '📊'), ('whale', '🐋')) if deal[t])
            gw_ch = find_channel(g0, 'giveaway')
            gw_ment = f"<#{gw_ch.id}>" if gw_ch else 'the giveaway room'
            body += (f"\n\n💎 **{cnts}** — the rest of this card is live in the paid rooms right now → {upg_ment}\n"
                     f"🛒 [Unlock the full board — every pick, every 4 hours](https://thelineshift.github.io/SHiFTS/upgrade.html?utm_source=discord_free)\n"
                     f"🎁 **Sunday 6 PM ET:** $50 in SOL, two winners — free entry in {gw_ment} ⚡")
        if await _room_already_posted(room, f"**{tier.upper()} ROOM — {slot_et} CARD**"):
            print(f'[scan] dedupe@room: {tier} {slot_et} card already posted — skipping repost')
        else:
            try:
                await room.send(embed=discord.Embed(description=body[:4090], color=TIER_COLORS.get(tier, 0x2B2D31)))
            except Exception as e:
                print(f'[scan] CARD SEND FAILED {tier}: {e}')
                try:
                    await lab.send(f"\u26a0\uFE0F **CARD SEND FAILED — {tier.upper()} ROOM {slot_et}:** `{e}` — retry law will re-fire the slot.")
                except Exception:
                    pass
                raise
        await asyncio.sleep(1)
    # ---- sanitized complete in general chat (NO-LEAK LAW) — clickable tags + upgrade funnel
    free_p = deal['free'][0] if deal['free'] else None
    free_ch = find_channel(g0, SCAN_ROOMS['free'])
    fp_ment = f'<#{free_ch.id}>' if free_ch else '#free-pick'
    comp = f"✅ {tag}**SCAN COMPLETE — {slot_et}**\n\n"
    if free_p:
        comp += f"🎯 **FREE: [{league_tag(free_p.get('sport'))}] {free_p['pick']} vs {free_p['vs']} — {free_p['units']}u** ({_et(free_p['start'])}) → live in {fp_ment}\n"
    else:
        comp += f"🎯 No free play this window — nothing met our edge bar, and we don't force bets. Next scan **{_nxt_et()}**.\n"
    cnts = ' · '.join(f"{e} +{len(deal[t])}" for t, e in (('lock', '🔒'), ('sharp', '📊'), ('whale', '🐋')) if deal[t])
    if cnts:
        comp += f"\n{cnts} — the full board is live in the paid rooms.\n💎 **Up to 36 picks a day** in Whale · **24** in Sharp · **18** in Lock — depending on the day's slate → {upg_ment}\n"
    comp += f"\n⏭️ **Next card drops {_nxt_et()}** — lock in early, lines move.\nEvery play before start. Every result receipted. ⚡"
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
    # ---- PLAY OF THE DAY (owner decree 2026-07-25): the single highest-edge play, 4 PM ET daily.
    # Full play in the paid rooms (tier integrity); teaser + X beat everywhere else.
    if not dry and int(slot_key.split('-')[1]) == 20:
        try:
            _pod_key = 'pod-' + slot_key[:8]
            st = await asyncio.to_thread(get_state)
            if (st.get('scan_events') or {}).get(_pod_key) != 'ok-bot':
                _pool = [c for c in cands if not c.get('parlay') and c.get('edge', 0) >= 0.06]
                _pod = max(_pool, key=lambda x: x.get('edge', 0)) if _pool else None
                if _pod:
                    _odds = _pod.get('odds')
                    _os = f" ({'+' if _odds and _odds > 0 else ''}{_odds:g})" if isinstance(_odds, (int, float)) else ''
                    _em = (f"⚡ **PLAY OF THE DAY**\n\n**[{league_tag(_pod.get('sport'))}] {_pod['pick']}{_os}** vs {_pod['vs']} — {_pod.get('units', 1)}u\n"
                           f"🕓 {_et(_pod['start'])} · edge **{_pod['edge']:.0%}** vs the number\n\n{_pod.get('analysis', '')}")
                    for _t in ('whale', 'sharp', 'lock'):  # whale first — tier depth law
                        _rm = find_channel(g0, SCAN_ROOMS[_t])
                        if _rm:
                            if await _room_already_posted(_rm, f"**PLAY OF THE DAY**\n\n**[{league_tag(_pod.get('sport'))}] {_pod['pick']}"):
                                print(f'[scan] dedupe@room: POD already in {_t}')
                            else:
                                await _rm.send(embed=discord.Embed(description=_em[:4090], color=TIER_COLORS.get(_t, 0xF5C518)))
                            await asyncio.sleep(1)
                    if gen:
                        await gen.send(f"⚡ **PLAY OF THE DAY** just dropped in the paid rooms — SHiFT's single highest-edge play, every day at 4 PM ET.\n"
                                       f"💎 Lock · Sharp · Whale → {upg_ment}")
                    try:
                        await asyncio.to_thread(x_post,
                            "⚡ PLAY OF THE DAY just dropped in the Discord — SHiFT's single highest-edge play, every day 4 PM ET.\n\n"
                            "Free picks daily + $50 in SOL every Sunday: https://thelineshift.github.io/SHiFTS/upgrade.html")
                    except Exception as _xe:
                        print('pod x:', _xe)
                    st.setdefault('scan_events', {})[_pod_key] = 'ok-bot'
                    await asyncio.to_thread(gh_put, 'bot_state.json', st, 'pod posted')
                    print('POD posted:', _pod['pick'])
        except Exception as e:
            print('pod:', e)
    # ---- CARD LAW (challenge): 4 PM ET scan feeds the 100-to-1000 channel, every day
    if os.environ.get('CHALLENGE_ON', '') == '1' and int(slot_key.split('-')[1]) == 20:  # challenge rail RETIRED 2026-07-25 — superseded by the trading desk
        try:
            await challenge_daily(g0, cands + ([parlay_built] if parlay_built else []), dry)
        except Exception as e:
            print('challenge feed:', e)
    print(f'scan engine {"dry" if dry else "LIVE"} slot {slot_key}: {len(picks_out)} plays posted')

_SCAN_DONE = set()
_SCAN_TRIES = {}

def ml_to_dec(ml):
    try:
        ml = int(ml)
        return 1 + (100 / abs(ml) if ml < 0 else ml / 100)
    except Exception:
        return None

def dec_to_ml(dec):
    if not dec or dec <= 1:
        return None
    return round((dec - 1) * 100) if dec >= 2 else -round(100 / (dec - 1))  # 7/25: the condition was inverted — favorites showed as tiny +odds

OP_KEY = os.environ.get('ODDSPAPI_KEY', '')
OP_SPORT = {'cs2': 17, 'lol': 18, 'valorant': 61, 'dota2': 16}
OP_MONTH_CAP = 230  # free tier is 250/mo — hard-stop with headroom

def op_fetch(path):
    """OddsPapi GET (browser UA; key via query param). Returns parsed json or None."""
    if not OP_KEY:
        return None
    try:
        sep = '&' if '?' in path else '?'
        req = urllib.request.Request(f'https://api.oddspapi.io{path}{sep}apiKey={OP_KEY}', headers={'User-Agent': 'Mozilla/5.0'})
        return json.loads(urllib.request.urlopen(req, timeout=15).read())
    except Exception as e:
        print('op_fetch:', str(e)[:100])
        return None

async def op_title_lines(title, st):
    """OddsPapi moneylines for one esports title -> [(name1, name2, dec1, dec2)].
    Meta (active tournaments + participant names) cached 7d; fixture lines cached 6h;
    every network call counts against the monthly free-tier budget in bot state."""
    if not OP_KEY:
        return []
    month = time.strftime('%Y-%m')
    if st.get('op_month') != month:
        st['op_month'], st['op_calls'] = month, 0
    meta = st.setdefault('op_meta', {}).setdefault(title, {})
    now = time.time()
    calls = [0]

    async def _call(path):
        if st.get('op_calls', 0) + calls[0] >= OP_MONTH_CAP:
            return None
        if now < st.get('op_cool_until', 0):
            return None  # recent 429 — free tier is burst-sensitive, back off 15 min
        calls[0] += 1
        r = await asyncio.to_thread(op_fetch, path)
        if r is None:
            st['op_cool_until'] = now + 900
        await asyncio.sleep(1.5)  # pace calls — the free tier 429s on bursts
        return r

    sid = OP_SPORT.get(title)
    if not sid:
        return []
    try:
        if now - meta.get('meta_ts', 0) > 7 * 86400:
            trn = await _call(f'/v4/tournaments?sportId={sid}')
            if trn is not None:
                meta['active'] = [t['tournamentId'] for t in trn
                                  if (t.get('upcomingFixtures') or 0) > 0 or (t.get('futureFixtures') or 0) > 0]
                parts = await _call(f'/v4/participants?sportId={sid}')
                if parts:
                    meta['parts'] = parts
                meta['meta_ts'] = now
        if now - meta.get('odds_ts', 0) < 6 * 3600 and meta.get('lines') is not None:
            st['op_calls'] = st.get('op_calls', 0) + calls[0]
            return meta['lines']
        lines = []
        ids = (meta.get('active') or [])[:15]
        for i in range(0, len(ids), 5):
            d = await _call('/v4/odds-by-tournaments?bookmaker=pinnacle&tournamentIds=' + ','.join(map(str, ids[i:i + 5])))
            for fx in (d or []):
                mk = (fx.get('bookmakerOdds', {}).get('pinnacle', {}) or {}).get('markets', {})
                ml_m = ([m for m in mk.values() if str(m.get('bookmakerMarketId', '')).endswith('/0/moneyline')]
                        or [m for m in mk.values() if 'moneyline' in str(m.get('bookmakerMarketId', ''))])
                if not ml_m:
                    continue
                hp = ap = None
                for o in ml_m[0].get('outcomes', {}).values():
                    pl = (o.get('players') or {}).get('0') or {}
                    if pl.get('bookmakerOutcomeId') == 'home':
                        hp = pl.get('price')
                    elif pl.get('bookmakerOutcomeId') == 'away':
                        ap = pl.get('price')
                n1 = (meta.get('parts') or {}).get(str(fx.get('participant1Id')))
                n2 = (meta.get('parts') or {}).get(str(fx.get('participant2Id')))
                if hp and ap and n1 and n2:
                    lines.append((n1, n2, float(hp), float(ap)))
        meta['lines'] = lines
        meta['odds_ts'] = now
        st['op_calls'] = st.get('op_calls', 0) + calls[0]
        return lines
    except Exception as e:
        print('op_title_lines:', e)
        st['op_calls'] = st.get('op_calls', 0) + calls[0]
        return meta.get('lines') or []

def op_match(lines, name_a, name_b):
    """Find (dec for name_a's team, dec for name_b's team) from OddsPapi lines."""
    na, nb = norm_txt(name_a), norm_txt(name_b)
    for n1, n2, d1, d2 in (lines or []):
        s1, s2 = norm_txt(n1), norm_txt(n2)
        def hit(q, s):
            return q and len(q) > 3 and (q in s or (len(s) > 3 and s in q))
        if hit(na, s1) and hit(nb, s2):
            return d1, d2
        if hit(na, s2) and hit(nb, s1):
            return d2, d1
    return None

_WHALE_CACHE = {}

def the_number(p):
    """One-line pricing read for paid cards: fair price vs book price, model vs implied, edge."""
    try:
        prob = p.get('prob') or 0
        odds = p.get('odds')
        if not prob or odds is None:
            return ''
        fair = dec_to_ml(1 / prob)
        fair_s = f'{fair:+d}' if isinstance(fair, int) else '—'
        return (f"fair **{fair_s}** vs book **{fmt_odds_num(odds)}** · model **{prob:.0%}** "
                f"vs implied **{_amer_prob(odds):.0%}** · edge **{p.get('edge', 0):.0%}**")
    except Exception:
        return ''


def se_whale_extras(c):
    """WHALE-ONLY premium intel: venue indoors/outdoors + weather + injury report.
    Top-tier customers get the full picture behind the pick. Cached per event."""
    eid = c.get('eid')
    sport = (c.get('sport') or '')
    if not eid or sport not in SE_SPORTS:
        return ''
    if eid in _WHALE_CACHE:
        return _WHALE_CACHE[eid]
    bits = []
    try:
        d = se_get('https://site.api.espn.com/apis/site/v2/sports/%s/summary?event=%s' % (SE_SPORTS[sport], eid))
        gi = d.get('gameInfo') or {}
        ven = gi.get('venue') or {}
        w = gi.get('weather') or {}
        vname = ven.get('fullName', '')
        if ven.get('indoor'):
            bits.append(f"🏟️ **Indoors** — {vname}: climate controlled, weather is a non-factor")
        else:
            cond = w.get('displayValue') or ''
            temp, wind = w.get('temperature'), w.get('windSpeed')
            loc = (ven.get('address') or {}).get('city', '')
            if temp is not None:
                bits.append(f"🌤️ **Outdoors** — {vname} ({loc}): {cond}, {temp}°F, wind {wind} mph")
            else:
                bits.append(f"🌤️ **Outdoors** — {vname} {loc} {cond}".strip())
    except Exception as e:
        print('whale venue:', e)
    try:
        ij = se_get('https://site.api.espn.com/apis/site/v2/sports/%s/injuries' % SE_SPORTS[sport])
        teams_n = {norm_txt(c.get('team', '')), norm_txt(c.get('opp', ''))}
        hits = []
        for t in ij.get('injuries', []):
            tn = norm_txt(t.get('team', {}).get('displayName', ''))
            if tn in teams_n or any(tn in x or x in tn for x in teams_n if x and len(x) > 4):
                for it in (t.get('injuries') or []):
                    st = (it.get('status') or '').lower()
                    if st in ('out', 'doubtful', 'questionable', 'day-to-day', 'suspended', 'injured reserve'):
                        hits.append(f"{(it.get('athlete') or {}).get('shortName', '?')} ({it.get('status')})")
        if hits:
            bits.append('🩹 **Injury report:** ' + ', '.join(hits[:6]))
        else:
            bits.append('🩹 **Injury report:** no key injuries flagged on either side')
    except Exception as e:
        print('whale injuries:', e)
    out = '\n'.join(bits)
    _WHALE_CACHE[eid] = out
    return out

def se_build_parlay(pool, rot):
    """Build one 2-3 leg cross-sport parlay from edge-qualified leftovers.
    Legs always from different games; sports mix naturally. Returns (parlay, room) or (None, None)."""
    legs = [c for c in pool if c.get('odds') is not None and ml_to_dec(c['odds'])]
    if len(legs) < 2:
        return None, None
    legs = legs[:3]  # 3 legs when the pool is deep, else 2
    dec = 1.0
    prob = 1.0
    for c in legs:
        dec *= ml_to_dec(c['odds'])
        prob *= c.get('prob') or 0.5
    ml = dec_to_ml(dec)
    if ml is None:
        return None, None
    names = ' + '.join(c['pick'] for c in legs)
    rooms = ['whale', 'sharp', 'lock']
    room = rooms[rot % len(rooms)]
    strong = all(c['edge'] >= 0.12 for c in legs)
    parlay = {'sport': 'parlay', 'pick': f"{len(legs)}-LEG PARLAY", 'vs': ' · '.join(f"{c['pick']} vs {c['vs']}" for c in legs),
              'odds': ml, 'units': 1.0 if strong and len(legs) == 2 else 0.5,
              'edge': prob - (1 / dec), 'start': min(c['start'] for c in legs), 'prob': prob,
              'market': f"{len(legs)}-leg parlay ({' + '.join(sorted({c['sport'].upper() for c in legs}))})",
              'analysis': ' ・ '.join(f"{c['pick']} ({fmt_odds_num(c['odds'])})" for c in legs),
              'parlay': True, 'legs': [{'pick': c['pick'], 'vs': c['vs'], 'sport': c['sport'],
                                        'start': c['start'], 'odds': c['odds']} for c in legs],
              'picks_desc': names}
    return parlay, room

async def show_room_picks(message):
    """Post the current card + last settled results for the room the member is standing in.
    Free/general channels get the free pick only; shift-lab gets the whole board."""
    try:
        cn = (getattr(message.channel, 'name', '') or '').lower().replace('-', '').replace('_', '')
        tier = None
        for t, rn in SCAN_ROOMS.items():
            if rn.replace('-', '') in cn:
                tier = t
                break
        all_tiers = 'shiftlab' in cn
        if tier is None and not all_tiers:
            tier = 'free'
        doc = await asyncio.to_thread(gh_get_json_ref, 'picks.json', 'main')
        picks = (doc or {}).get('picks', [])
        today = time.strftime('%Y-%m-%d', time.gmtime(time.time() - 4 * 3600))

        def _line(p):
            e = {'WIN': '✅', 'LOSS': '❌', 'PUSH': '🟰'}.get(p.get('result'), '⏳')
            o = fmt_odds_num(p['odds']) if isinstance(p.get('odds'), int) else (p.get('odds') or '')
            tail = ''
            if p.get('result'):
                u = p.get('units_result') or 0
                tail = f" · {p['result']} {'+' if u > 0 else ''}{u:.2f}u"
            return f"{e} **{p.get('desc')}** ({o}) — {p.get('time_et', '')}{tail}"

        chunks = []
        for t in (['whale', 'sharp', 'lock', 'free'] if all_tiers else [tier]):
            tp = [p for p in picks if p.get('tier') == t]
            live = [p for p in tp if not p.get('result') and (p.get('date') or '') >= today]
            done = [p for p in tp if p.get('result')][-3:]
            if not live and not done:
                continue
            body = ''
            if live:
                body += '**Current card:**\n' + '\n'.join(_line(p) for p in live[-4:])
            if done:
                body += ('\n' if body else '') + '**Last results:**\n' + '\n'.join(_line(p) for p in done)
            chunks.append(f"{TIER_BADGE.get(t, t)}\n{body}")
        if not chunks:
            await message.channel.send(f"{message.author.mention} Nothing on this room's board yet today — the next scan deals at **12a · 4a · 8a · 12p · 4p · 8p ET**. ⚡")
            return
        await message.channel.send(f"{message.author.mention}\n\n" + '\n\n'.join(chunks)[:1900])
    except Exception as e:
        print('show_room_picks:', e)

def heal_pick_teams(p, sb):
    """Backfill awayTeam/homeTeam for picks registered without them (the receipts-killer
    of 7/23-24): match the pick's team text against that day's scoreboard events."""
    try:
        desc_n = norm_txt(p.get('desc', ''))
        best = None
        for ev in sb.get('events', []):
            comp = ev['competitions'][0]
            teams = {c['homeAway']: c for c in comp['competitors']}
            an = norm_txt(teams['away']['team'].get('displayName', ''))
            hn = norm_txt(teams['home']['team'].get('displayName', ''))
            score = 0
            for tok in (an, hn):
                if tok and (tok in desc_n or any(t in desc_n for t in tok.split() if len(t) > 3)):
                    score += 1
            # word-level fallback for names like "Milwaukee Brewers"
            for w in re.findall(r'[a-z]{4,}', (p.get('desc') or '').lower()):
                if w in an or w in hn:
                    score += 1
            if score and (best is None or score > best[0]):
                best = (score, teams['away']['team'].get('displayName'), teams['home']['team'].get('displayName'))
        if best and best[0] >= 1:
            return best[1], best[2]
    except Exception:
        pass
    return None, None

_PS_PAST_CACHE = {}

async def ps_settle_leg_detail(team, sport, date_et):
    """Settle one esports side via PandaScore past matches.
    Returns {'result': 'win'/'loss'/'push', 'opp': name, 'score': 's1-s2', 'bo': n} or None."""
    game = PS_GAMES.get((sport or '').lower())
    if not game or not team:
        return None
    try:
        ts, ms = _PS_PAST_CACHE.get(game, (0, None))
        if ms is None or time.time() - ts > 600:
            ms = await asyncio.to_thread(se_ps, f'/{game}/matches/past', per_page=100)
            _PS_PAST_CACHE[game] = (time.time(), ms)
    except Exception as e:
        print('ps settle fetch:', e)
        return None
    tn = norm_txt(team)
    for m in ms or []:
        try:
            if m.get('status') != 'finished':
                continue
            opps = m.get('opponents') or []
            if len(opps) < 2:
                continue
            names = [norm_txt((o.get('opponent') or {}).get('name', '')) for o in opps]
            hit = None
            for i, nn in enumerate(names):
                if tn and len(tn) >= 3 and (tn in nn or (len(nn) >= 3 and nn in tn)):
                    hit = i
                    break
            if hit is None:
                continue
            ba = m.get('begin_at') or m.get('scheduled_at') or ''
            try:
                mts = time.mktime(time.strptime(ba[:19], '%Y-%m-%dT%H:%M:%S'))
                m_date = time.strftime('%Y-%m-%d', time.gmtime(mts - 4 * 3600))
            except Exception:
                continue
            if date_et and m_date != date_et:
                continue
            other = (opps[1 - hit].get('opponent') or {})
            t_id = (opps[hit].get('opponent') or {}).get('id')
            o_id = other.get('id')
            sc = ''
            s_map = {r.get('team_id'): r.get('score') for r in (m.get('results') or [])}
            if t_id in s_map and o_id in s_map:
                sc = f"{s_map[t_id]}-{s_map[o_id]}"
            win_id = m.get('winner_id')
            result = 'push' if win_id is None else ('win' if t_id == win_id else 'loss')
            return {'result': result, 'opp': other.get('name', ''), 'score': sc, 'bo': m.get('number_of_games')}
        except Exception:
            continue
    return None

async def ps_settle_leg(team, sport, date_et):
    """Settle one esports side: 'win'/'loss'/'push'/None (thin wrapper over the detail call)."""
    d = await ps_settle_leg_detail(team, sport, date_et)
    return d['result'] if d else None

async def grade_parlays(guild):
    """Settle registered parlays: every leg final -> WIN all / LOSS any / PUSH mix."""
    try:
        doc = await asyncio.to_thread(gh_get_json_ref, 'picks.json', 'main')
        if not doc:
            return
        changed = False
        settled = []
        for p in (doc.get('picks') or []):
            if p.get('sport') != 'parlay' or str(p.get('result', '')) not in ('', 'PENDING', 'None', 'NONE'):
                continue
            legs = p.get('legs') or []
            if not legs:
                continue
            leg_results = []
            for lg in legs:
                try:
                    if (lg.get('sport') or '').lower() in PS_GAMES:
                        leg_results.append(await ps_settle_leg(lg.get('team'), lg.get('sport'), lg.get('date')))
                        continue
                    sb = await asyncio.to_thread(espn_fetch, (lg.get('sport') or '').upper(), (lg.get('date') or '').replace('-', ''))
                    team_n, opp_n = norm_txt(lg.get('team', '')), norm_txt(lg.get('vs', ''))
                    found = None
                    for ev in sb.get('events', []):
                        comp = ev['competitions'][0]
                        comps = comp.get('competitors') or []
                        names = [norm_txt(c2.get('team', {}).get('displayName', '')) for c2 in comps]
                        if team_n and opp_n and any(team_n in n for n in names) and any(opp_n in n for n in names):
                            if not comp.get('status', {}).get('type', {}).get('completed'):
                                found = 'pending'
                                break
                            sc = {c2.get('team', {}).get('displayName', ''): int(float(c2.get('score') or 0)) for c2 in comps}
                            t_score = next((v for k, v in sc.items() if team_n in norm_txt(k)), None)
                            o_score = next((v for k, v in sc.items() if opp_n in norm_txt(k)), None)
                            if t_score is None or o_score is None:
                                found = 'pending'
                                break
                            found = 'win' if t_score > o_score else ('push' if t_score == o_score else 'loss')
                            break
                    leg_results.append(found)
                except Exception:
                    leg_results.append(None)
            if not leg_results or any(r in (None, 'pending') for r in leg_results):
                continue
            if 'loss' in leg_results:
                res = 'LOSS'
            elif all(r == 'win' for r in leg_results):
                res = 'WIN'
            else:
                res = 'PUSH'
            p['result'] = res
            dec = ml_to_dec(p.get('odds')) or 2.0
            u = float(p.get('units') or 0.5)
            p['units_result'] = round(u * (dec - 1), 2) if res == 'WIN' else (-u if res == 'LOSS' else 0.0)
            p['score'] = ' · '.join(f"{lg.get('pick')}: {r}" for lg, r in zip(legs, leg_results))
            changed = True
            settled.append(p)
        if not changed:
            return
        doc['updated'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        await asyncio.to_thread(gh_put, 'picks.json', doc, 'parlay settle: ' + ', '.join(p['id'] for p in settled), 'main')
        ch = find_channel(guild, 'receipts')
        state = await asyncio.to_thread(get_state)
        for p in settled:
            e = '✅' if p['result'] == 'WIN' else ('🟰' if p['result'] == 'PUSH' else '❌')
            u = p.get('units_result', 0)
            us = f'+{u}u' if u > 0 else f'{u}u'
            badge = TIER_BADGE.get(p.get('tier'), '')
            if ch:
                _tw, _tl, _tu = tier_season_line(doc.get('picks', []), p.get('tier'))
                await ch.send(f"🧾 **RESULT {badge} PARLAY:** {p.get('desc')} ({fmt_odds_num(p.get('odds')) if isinstance(p.get('odds'), int) else 'ML'}) {e} **{p['result']}** {us}\nLegs: {p.get('score')}\n"
                              f"📊 {str(p.get('tier') or '').upper()} season: **{_tw}-{_tl}** ({'+' if _tu >= 0 else ''}{_tu:.1f}u) — every play receipted")
            if state is not None:
                state.setdefault('unannounced_results', []).append(
                    {'id': p['id'], 'desc': p.get('desc'), 'odds': p.get('odds'), 'result': p['result'],
                     'units': p.get('units_result'), 'score': 'Legs: ' + p.get('score', ''), 'tier': p.get('tier')})
        if state is not None:
            await asyncio.to_thread(gh_put, 'bot_state.json', state, 'parlay results')
        for p in settled:
            if p.get('tier') == 'challenge':
                await settle_challenge(guild, p)
    except Exception as e:
        print('grade_parlays error:', e)

def _money_e(bal):
    return '💰' if bal >= 500 else ('💵' if bal >= 100 else ('💸' if bal >= 40 else '🪙'))

async def challenge_daily(g0, cands, dry):
    """CARD LAW (challenge): the 100-to-1000 channel gets action every single day —
    up to 2 straight bets + 1 parlay from the 4 PM ET scan; never silent."""
    try:
        chal = await asyncio.to_thread(gh_get_json_ref, 'challenge.json', 'main')
        if not chal:
            return
        now_et = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=4)
        today_et = now_et.strftime('%Y-%m-%d')
        plays = chal.setdefault('plays', [])
        if any(pl.get('date') == today_et for pl in plays):
            return  # today's action already posted
        bal = float(chal.get('balance', 100))
        # OWNER DECREE: no fixed stake rule — edge-scaled strategy. Bigger edge = bigger
        # press (10% floor, 40% ruin-guard ceiling). Parlays stay small lottos (8%).
        def _stake_for(edge):
            # DEGEN YOLO PROFILE (owner decree 2026-07-25): r/wallstreetbets sizing —
            # 25% floor, 60% ceiling, press the edge, never turtle.
            return max(1.0, round(bal * min(0.60, max(0.25, 0.25 + edge * 1.5)), 2))
        espn_c = [c for c in cands if (c.get('sport') or '').upper() in ESPN and c.get('odds') is not None]
        # dry sims rehearse the full challenge feed into shift-lab — silent to members
        ch = find_channel(g0, 'shift-lab') if dry else find_channel(g0, '100-to-1000')
        if not ch:
            return
        n0 = max([pl.get('n', 0) for pl in plays] or [0])
        posted = 0
        picks_add = []
        for c in espn_c[:2]:
            stake = _stake_for(c['edge'])
            o = int(c['odds'])
            to_win = round(stake * (100 / abs(o) if o < 0 else o / 100), 2)
            n0 += 1
            t_et = _et(c['start'])
            plays.append({'n': n0, 'date': today_et, 'pick': c['pick'], 'odds': o, 'stake': stake,
                          'toWin': to_win, 'time_et': t_et, 'result': None,
                          'posted': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                          'note': f"auto challenge — edge {c['edge']:.0%}, stake {min(0.60, max(0.25, 0.25 + c['edge'] * 1.5)):.0%} of bankroll (degen yolo profile); {c.get('analysis','')[:70]}"})
            picks_add.append({'id': f"challenge-{c['pick'].lower().replace(' ', '-')[:24]}-{today_et[5:].replace('-', '')}",
                              'date': today_et, 'sport': c['sport'], 'desc': c['pick'], 'market': c.get('market', 'ML'),
                              'odds': o, 'units': 1.0, 'tier': 'challenge', 'time_et': t_et,
                              'analysis': c.get('analysis', '')[:300]})
            posted += 1
            await ch.send(f"💵 {'[DRY] ' if dry else ''}**CHALLENGE BET #{n0}** — 💲**${stake:.2f}** on [{league_tag(c.get('sport'))}] **{c['pick']}** ({fmt_odds_num(o)}) vs {c['vs']}\n"
                          f"📊 {c.get('analysis','')}\n"
                          f"To win **${to_win:.2f}** · balance {_money_e(bal)} **${bal:.2f}** → 💰 **$1,000** goal · {_et(c['start'])} ET ⚡")
            await asyncio.sleep(1)
            if PM_LIVE and PM_LIVE_CAP > 0 and not dry:
                try:
                    team_q = c['pick'][:-3] if c['pick'].endswith(' ML') else c['pick']
                    info = await asyncio.to_thread(pm_find_market, team_q, c['vs'], c.get('start', ''))
                    if info:
                        live = await asyncio.to_thread(pm_place_bet, info, min(stake, PM_LIVE_CAP))
                        if 'order_id' in live:
                            live.update({'league': league_tag(c.get('sport')), 'start': info.get('start', ''),
                                         'marketSlug': info['marketSlug'], 'outcome': info['outcome'],
                                         'title': info['title'],
                                         'placed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
                            st = await asyncio.to_thread(get_state)
                            st.setdefault('pm_live', []).append(live)
                            st['pm_live'] = st['pm_live'][-40:]
                            await asyncio.to_thread(gh_put, 'bot_state.json', st, 'pm live bet placed')
                            try:
                                import io as _io2
                                _slip = await asyncio.to_thread(pm_slip_png, live, 'LIVE')
                                await ch.send(f"🪙 **LIVE BET:** ${live['stake']:.2f} real on **{_trade_label(live)}** @ {live['price']:.2f} "
                                              f"({live['qty']} shares) — Polymarket US",
                                              file=discord.File(_io2.BytesIO(_slip), filename='bet-slip.png'))
                            except Exception as _se:
                                print('slip render:', _se)
                                await ch.send(f"🪙 **LIVE BET:** ${live['stake']:.2f} real on **{_trade_label(live)}** @ {live['price']:.2f} "
                                              f"({live['qty']} shares) — Polymarket US")
                        elif live.get('error') not in ('no_liquidity', 'below_min'):
                            print(f"[challenge] pm place skipped: {live.get('error')}")
                except Exception as e:
                    print(f"[challenge] pm live hook: {e}")
        # parlay kicker: when a built parlay exists, challenge takes a 10%-of-balance shot
        parlays = [c for c in cands if c.get('parlay')]
        if parlays:
            c = parlays[0]
            p_stake = max(1.0, round(bal * 0.12, 2))  # DEGEN YOLO: the lotto ticket always rides
            p_dec = ml_to_dec(c['odds']) or 2.0
            p_win = round(p_stake * (p_dec - 1), 2)
            n0 += 1
            t_et = _et(c['start'])
            plays.append({'n': n0, 'date': today_et, 'pick': c['picks_desc'], 'odds': c['odds'], 'stake': p_stake,
                          'toWin': p_win, 'time_et': t_et, 'result': None, 'parlay': True,
                          'posted': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                          'note': f"challenge parlay — {c['market']}"})
            picks_add.append({'id': f"challenge-parlay-{today_et[5:].replace('-', '')}", 'date': today_et,
                              'sport': 'parlay', 'desc': f"PARLAY: {c['picks_desc']}", 'market': c['market'],
                              'odds': c['odds'], 'units': 0.5, 'tier': 'challenge', 'time_et': t_et,
                              'analysis': c.get('analysis', '')[:300],
                              'legs': [{'team': lg['pick'][:-3] if lg['pick'].endswith(' ML') else lg['pick'],
                                        'vs': lg['vs'], 'sport': lg['sport'], 'date': today_et,
                                        'time_et': _et(lg['start'])} for lg in c['legs']]})
            await ch.send(f"💵 {'[DRY] ' if dry else ''}**CHALLENGE PARLAY #{n0}** — ${p_stake:.2f} lotto ticket 🎰\n"
                              + '\n'.join(f"• [{league_tag(lg.get('sport'))}] {lg['pick']} ({fmt_odds_num(lg['odds'])}) vs {lg['vs']}" for lg in c['legs']) +
                              f"\nCombined **{fmt_odds_num(c['odds'])}** — to win **${p_win:.2f}** · balance {_money_e(bal)} **${bal:.2f}** → 💰 $1,000 ⚡")
        if not posted and not parlays:
            await ch.send(f"💵 {'[DRY] ' if dry else ''}**CHALLENGE — {today_et}**: slate too thin for a qualified edge today. "
                              f"Bankroll stays {_money_e(bal)} **${bal:.2f}** — discipline is how 💵 $100 becomes 💰 $1,000. Next scan 8 PM ET. ⚡")
            return
        chal['updated'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        if not dry:
            await asyncio.to_thread(gh_put, 'challenge.json', chal, f'challenge daily {today_et}', 'main')
            if picks_add:
                pj = await asyncio.to_thread(gh_get_json_ref, 'picks.json', 'main') or {'picks': []}
                have = {p['id'] for p in pj.get('picks', [])}
                pj['picks'] = pj.get('picks', []) + [p for p in picks_add if p['id'] not in have]
                pj['updated'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                await asyncio.to_thread(gh_put, 'picks.json', pj, f'challenge picks {today_et}', 'main')
    except Exception as e:
        print('challenge_daily error:', e)

@tasks.loop(hours=24)
async def issues_sweep():
    """ISSUES PRIVACY LAW: the issues room clears daily so no customer's private info
    lingers in a public channel — but NEVER mid-issue: anything under 24h old stays."""
    try:
        g0 = client.guilds[0] if client.guilds else None
        ch = find_channel(g0, 'issues') if g0 else None
        if not ch:
            return
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=24)
        deleted = 0
        async for m in ch.history(limit=200):
            if m.created_at < cutoff:
                try:
                    await m.delete()
                    deleted += 1
                    await asyncio.sleep(0.6)
                except Exception:
                    pass
        if deleted:
            print(f'issues_sweep: cleared {deleted} message(s) older than 24h')
    except Exception as e:
        print('issues_sweep error:', e)

@tasks.loop(minutes=17)
async def x_purge_old():
    """Standing owner order: wipe ALL pre-SHiFT content back to account creation —
    posts, replies, quotes, reposts, everything (explicit owner decree, credits accepted).
    Ticks every 17 min, up to 40 deletes per tick (~50/window cap; card posts + receipts
    need the headroom). Brand era = 2026-07-01 onward — never touched.
    Marks x_purge_complete when the window comes back clean."""
    if os.environ.get('X_PURGE_OLD', '') != '1':
        return
    try:
        st0 = await asyncio.to_thread(get_state)
        done_ts = (st0 or {}).get('x_purge_complete')
        if done_ts and time.time() - done_ts < 86400:
            return  # verified clean within the last 24h — don't burn read calls
        c = x_creds_load()
        if time.time() > c.get('oauth2_expires_at', 0):
            try:
                c = await asyncio.to_thread(x_oauth2_refresh, c)
            except Exception as e:
                print('x_purge_old: token refresh failed:', e)
                return
        def _fetch():
            req = urllib.request.Request(
                'https://api.x.com/2/users/1831457082828021760/tweets?max_results=100&tweet.fields=created_at,referenced_tweets',
                headers={'Authorization': f"Bearer {c['bearer_token']}"})
            return json.loads(urllib.request.urlopen(req, timeout=20).read())
        d = await asyncio.to_thread(_fetch)
        victims = []
        for t in d.get('data', []):
            if (t.get('created_at') or '') >= '2026-07-01':
                continue  # brand era — never touched
            # OWNER DECREE (2026-07-24, overrides credit-saving scope): delete EVERYTHING
            # pre-SHiFT — posts, replies, quotes, reposts — back to account creation.
            victims.append(t['id'])
            if len(victims) >= 40:
                break
        if not victims:
            if d.get('data') is not None:
                st0['x_purge_complete'] = time.time()
                await asyncio.to_thread(gh_put, 'bot_state.json', st0, 'x purge complete — timeline clean back to account creation')
                print('x_purge_old: COMPLETE — no pre-brand posts left in window')
            return
        ok_ct = 0
        for tid in victims:
            def _del():
                req = urllib.request.Request(f'https://api.x.com/2/tweets/{tid}', method='DELETE',
                                             headers={'Authorization': f"Bearer {c['oauth2_access']}"})
                return json.loads(urllib.request.urlopen(req, timeout=20).read())
            try:
                await asyncio.to_thread(_del)
                ok_ct += 1
            except Exception as e:
                if '429' in str(e):
                    break  # write cap reached — next tick resumes
                print('x_purge_old delete fail:', tid, e)
            await asyncio.sleep(1)
        if ok_ct:
            st = await asyncio.to_thread(get_state)
            st['x_purge_total'] = st.get('x_purge_total', 0) + ok_ct
            st['x_purge_last'] = time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())
            await asyncio.to_thread(gh_put, 'bot_state.json', st, 'x purge tick')
        print(f'x_purge_old: deleted {ok_ct} this tick')
    except Exception as e:
        print('x_purge_old error:', e)

async def do_sol_transfer(sol, to):
    """Send SOL from the ops wallet. Returns (sig, None) or (None, error)."""
    try:
        sol = float(sol)
    except Exception:
        return None, 'bad amount'
    if sol <= 0 or sol > 50:
        return None, 'amount out of bounds (0<x<=50)'
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
            return None, f'insufficient balance ({bal/1e9:.4f} SOL)'
        bh = (await asyncio.to_thread(_rpc, 'getLatestBlockhash', [{'commitment': 'finalized'}]))['result']['value']['blockhash']
        ix = transfer(TransferParams(from_pubkey=kp.pubkey(), to_pubkey=dest, lamports=lamports))
        tx = Transaction.new_signed_with_payer([ix], kp.pubkey(), [kp], SHash.from_string(bh))
        sig = (await asyncio.to_thread(_rpc, 'sendTransaction',
               [base64.b64encode(bytes(tx)).decode(), {'encoding': 'base64', 'skipPreflight': False}])).get('result')
        return sig, None
    except Exception as e:
        return None, str(e)

async def _room_already_posted(room, signature, limit=20):
    """DESTINATION-SIDE IDEMPOTENCY — the underlying double-post fix.
    state-file dedupe markers can be clobbered by racing loops (last-writer-wins);
    the room itself is the only ledger that can't lie. Skip if the signature is already up."""
    try:
        async for msg in room.history(limit=limit):
            if signature in (msg.content or ''):
                return True
            for emb in msg.embeds:
                if emb.description and signature in emb.description:
                    return True
    except Exception as e:
        print('room dedupe check:', e)
    return False


@tasks.loop(minutes=1)
async def scan_engine():
    try:
        now = time.gmtime()
        if now.tm_hour not in SCAN_SLOTS_UTC:
            return
        # CARD LAW: the card must drop, period. On-time fire at :00-:03; if the slot is
        # still unmarked (crash/restart/delays), fire late anywhere inside the slot hour.
        if now.tm_min > 55:
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
        try:
            await scan_engine_run(g0, key, dry)
            _SCAN_TRIES.pop(key, None)
        except Exception:
            # CARD LAW: a failed run must not silently kill the slot — retry next minute,
            # capped at 3 attempts per slot per boot so a hard failure can't spam rooms.
            _SCAN_TRIES[key] = _SCAN_TRIES.get(key, 0) + 1
            if _SCAN_TRIES[key] < 3:
                _SCAN_DONE.discard(key)
            raise
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
