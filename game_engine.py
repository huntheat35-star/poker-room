"""
Texas Hold'em NL — Production Game Engine
Proper PokerStars-style rules.
"""
import random, time, uuid
from enum import Enum
from dataclasses import dataclass, field
from collections import Counter

SUITS = ["s","h","d","c"]
RANKS = ["2","3","4","5","6","7","8","9","T","J","Q","K","A"]
RANK_VAL = {r:i+2 for i,r in enumerate(RANKS)}

@dataclass(frozen=True)
class Card:
    rank: str; suit: str
    def to_dict(self): return {"rank":self.rank,"suit":self.suit}
    @property
    def val(self): return RANK_VAL[self.rank]

def make_deck():
    d = [Card(r,s) for s in SUITS for r in RANKS]
    random.shuffle(d)
    return d

# ── Hand evaluation ──
HAND_NAME = {10:"Роял-флеш",9:"Стріт-флеш",8:"Каре",7:"Фул-хаус",
    6:"Флеш",5:"Стріт",4:"Трійка",3:"Дві пари",2:"Пара",1:"Старша карта"}

def _c5(items, k):
    if k == 0: yield []; return
    for i in range(len(items)):
        for c in _c5(items[i+1:], k-1): yield [items[i]] + c

def _score5(cards):
    v = sorted([c.val for c in cards], reverse=True)
    s = [c.suit for c in cards]
    flush = len(set(s)) == 1
    u = sorted(set(v), reverse=True)
    straight = False; hi = 0
    if len(u) == 5:
        if u[0] - u[4] == 4: straight = True; hi = u[0]
        if u == [14,5,4,3,2]: straight = True; hi = 5
    ct = Counter(v)
    g = sorted(ct.items(), key=lambda x: (x[1], x[0]), reverse=True)
    p = tuple(x[1] for x in g); gv = [x[0] for x in g]
    if flush and straight: return (10 if hi == 14 else 9, [hi])
    if p == (4,1): return (8, gv)
    if p == (3,2): return (7, gv)
    if flush: return (6, v)
    if straight: return (5, [hi])
    if p == (3,1,1): return (4, gv)
    if p == (2,2,1): return (3, gv)
    if p == (2,1,1,1): return (2, gv)
    return (1, v)

def best_hand(cards):
    b = (0, [])
    for c in _c5(cards, 5):
        r = _score5(c)
        if r > b: b = r
    return (b[0], b[1], HAND_NAME.get(b[0], "?"))

# ── Data classes ──
class Phase(str, Enum):
    WAITING = "waiting"
    PREFLOP = "preflop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN = "showdown"

@dataclass
class Player:
    uid: str
    name: str
    stack: float = 0
    hand: list = field(default_factory=list)
    bet: float = 0        # current street bet
    total_bet: float = 0   # total this hand
    folded: bool = False
    all_in: bool = False
    acted: bool = False
    seat: int = 0
    connected: bool = True
    action: str = ""       # last action text
    sitting_out: bool = False

    def reset(self):
        self.hand = []; self.bet = 0; self.total_bet = 0
        self.folded = False; self.all_in = False
        self.acted = False; self.action = ""

    def serialize(self, reveal=False, is_me=False):
        return {
            "uid": self.uid, "name": self.name, "stack": round(self.stack, 2),
            "bet": round(self.bet, 2), "folded": self.folded,
            "all_in": self.all_in, "seat": self.seat,
            "connected": self.connected, "action": self.action,
            "sitting_out": self.sitting_out,
            "hand": [c.to_dict() for c in self.hand] if (is_me or reveal) else
                    [{"rank":"?","suit":"?"} for _ in self.hand],
        }

@dataclass
class Table:
    id: str
    players: list = field(default_factory=list)
    max_seats: int = 9
    sb: float = 1
    bb: float = 2
    currency: str = "$"
    min_buyin: float = 20
    max_buyin: float = 200

    deck: list = field(default_factory=list)
    board: list = field(default_factory=list)
    pot: float = 0
    street_bet: float = 0    # current highest bet on this street
    min_raise: float = 0     # minimum raise increment
    phase: Phase = Phase.WAITING
    btn: int = 0             # dealer button
    turn: int = -1           # whose turn
    hand_num: int = 0
    sb_seat: int = -1
    bb_seat: int = -1
    winners: list = field(default_factory=list)
    results: dict = field(default_factory=dict)

    def get(self, uid):
        return next((p for p in self.players if p.uid == uid), None)

    def alive(self):
        """Players still in hand"""
        return [p for p in self.players if not p.folded and not p.sitting_out]

    def can_act(self):
        """Players who can make decisions"""
        return [p for p in self.players if not p.folded and not p.all_in and not p.sitting_out]

    def in_hand(self):
        """Players dealt into this hand"""
        return [p for p in self.players if not p.sitting_out]

    def _next(self, fr, skip_folded=True, skip_allin=True, skip_sitout=True):
        n = len(self.players)
        for i in range(1, n+1):
            idx = (fr + i) % n
            p = self.players[idx]
            if skip_sitout and p.sitting_out: continue
            if skip_folded and p.folded: continue
            if skip_allin and p.all_in: continue
            return idx
        return -1

    def serialize(self, viewer=None):
        show = self.phase == Phase.SHOWDOWN
        return {
            "id": self.id, "phase": self.phase.value,
            "board": [c.to_dict() for c in self.board],
            "pot": round(self.pot, 2), "street_bet": round(self.street_bet, 2),
            "min_raise": round(self.min_raise, 2),
            "btn": self.btn, "turn": self.turn,
            "hand_num": self.hand_num,
            "sb": self.sb, "bb": self.bb, "currency": self.currency,
            "sb_seat": self.sb_seat, "bb_seat": self.bb_seat,
            "winners": self.winners, "results": self.results,
            "players": [p.serialize(reveal=show, is_me=(p.uid == viewer)) for p in self.players],
        }

# ── Engine ──
class Engine:
    def __init__(self, table: Table):
        self.t = table

    def join(self, uid, name, buyin):
        t = self.t
        existing = t.get(uid)
        if existing:
            if existing.sitting_out:
                existing.sitting_out = False
                existing.stack = max(t.min_buyin, min(buyin, t.max_buyin))
                existing.connected = True
                return {"ok": "rebuy"}
            return {"error": "Ви вже за столом"}
        if len([p for p in t.players if not p.sitting_out]) >= t.max_seats:
            return {"error": "Стіл повний"}
        buyin = max(t.min_buyin, min(buyin, t.max_buyin))
        t.players.append(Player(uid=uid, name=name, stack=buyin, seat=len(t.players)))
        return {"ok": "joined"}

    def leave(self, uid):
        t = self.t
        p = t.get(uid)
        if not p: return
        if t.phase in (Phase.WAITING, Phase.SHOWDOWN):
            t.players = [x for x in t.players if x.uid != uid]
            for i, x in enumerate(t.players): x.seat = i
        else:
            p.sitting_out = True; p.folded = True

    def rebuy(self, uid, amount):
        t = self.t
        p = t.get(uid)
        if not p: return {"error": "Не за столом"}
        if t.phase not in (Phase.WAITING, Phase.SHOWDOWN):
            return {"error": "Дочекайтесь кінця роздачі"}
        amount = max(t.min_buyin, min(amount, t.max_buyin))
        p.stack += amount; p.sitting_out = False
        return {"ok": "rebuy"}

    def can_deal(self):
        return len([p for p in self.t.players if p.stack > 0 and not p.sitting_out]) >= 2

    # ── DEAL NEW HAND ──
    def deal(self):
        t = self.t
        if not self.can_deal(): return {"error": "Мінімум 2 гравці"}

        t.hand_num += 1
        t.deck = make_deck(); t.board = []
        t.pot = 0; t.street_bet = 0
        t.winners = []; t.results = {}

        # Sit out busted players
        for p in t.players:
            if p.stack <= 0: p.sitting_out = True
            p.reset()
            if p.sitting_out: p.folded = True

        playing = t.in_hand()
        n = len(playing)
        if n < 2: return {"error": "Мінімум 2 гравці"}

        # Move button
        t.btn = t._next(t.btn, skip_folded=False, skip_allin=False)

        # Blinds — heads-up: button=SB, other=BB
        if n == 2:
            t.sb_seat = t.btn
            t.bb_seat = t._next(t.btn, skip_folded=False, skip_allin=False)
        else:
            t.sb_seat = t._next(t.btn, skip_folded=False, skip_allin=False)
            t.bb_seat = t._next(t.sb_seat, skip_folded=False, skip_allin=False)

        # Deal cards
        for _ in range(2):
            for p in t.players:
                if not p.sitting_out:
                    p.hand.append(t.deck.pop())

        # Post blinds
        sbp = t.players[t.sb_seat]; bbp = t.players[t.bb_seat]
        sb_amt = min(t.sb, sbp.stack)
        sbp.stack -= sb_amt; sbp.bet = sb_amt; sbp.total_bet = sb_amt
        if sbp.stack == 0: sbp.all_in = True

        bb_amt = min(t.bb, bbp.stack)
        bbp.stack -= bb_amt; bbp.bet = bb_amt; bbp.total_bet = bb_amt
        if bbp.stack == 0: bbp.all_in = True

        t.pot = sb_amt + bb_amt
        t.street_bet = bb_amt
        t.min_raise = t.bb
        t.phase = Phase.PREFLOP

        # First to act: after BB (BB acts last preflop)
        first = t._next(t.bb_seat)
        if first == -1:
            # All in from blinds
            self._runout()
            return self._showdown()

        t.turn = first
        return {"ok": "dealt"}

    # ── PLAYER ACTION ──
    def act(self, uid, action, amount=0):
        t = self.t
        if t.phase in (Phase.WAITING, Phase.SHOWDOWN):
            return {"error": "Немає роздачі"}
        if t.turn < 0 or t.turn >= len(t.players):
            return {"error": "Помилка ходу"}
        p = t.players[t.turn]
        if p.uid != uid:
            return {"error": "Не ваш хід"}

        if action == "fold":   return self._fold(p)
        if action == "check":  return self._check(p)
        if action == "call":   return self._call(p)
        if action == "raise":  return self._raise(p, amount)
        if action == "allin":  return self._allin(p)
        return {"error": "Невідома дія"}

    def _fold(self, p):
        p.folded = True; p.acted = True; p.action = "fold"
        alive = self.t.alive()
        if len(alive) == 1: return self._win_fold(alive[0])
        return self._next_turn()

    def _check(self, p):
        if self.t.street_bet > p.bet:
            return {"error": "Є ставка — чек неможливий"}
        p.acted = True; p.action = "check"
        return self._next_turn()

    def _call(self, p):
        t = self.t
        to_call = min(t.street_bet - p.bet, p.stack)
        if to_call <= 0: return self._check(p)
        p.stack -= to_call; p.bet += to_call; p.total_bet += to_call
        t.pot += to_call
        if p.stack == 0: p.all_in = True
        p.acted = True; p.action = "call"
        return self._next_turn()

    def _raise(self, p, amount):
        t = self.t
        # amount = raise TO (total bet this street)
        min_to = t.street_bet + t.min_raise
        max_to = p.stack + p.bet

        if amount < min_to:
            # All-in for less is ok
            if max_to < min_to:
                return self._allin(p)
            return {"error": f"Мін. рейз: {t.currency}{min_to:.0f}"}

        amount = min(amount, max_to)  # cap at all-in
        cost = amount - p.bet
        actual = min(cost, p.stack)

        raise_size = amount - t.street_bet
        t.min_raise = max(t.min_raise, raise_size)  # next min raise
        t.street_bet = amount

        p.stack -= actual; p.bet += actual; p.total_bet += actual
        t.pot += actual
        if p.stack == 0: p.all_in = True

        # Reopen action for everyone else
        for x in t.players:
            if x is not p and not x.folded and not x.all_in and not x.sitting_out:
                x.acted = False

        p.acted = True; p.action = "raise"
        return self._next_turn()

    def _allin(self, p):
        t = self.t
        if p.stack <= 0: return {"error": "Немає фішок"}
        amt = p.stack
        old_bet = p.bet
        p.bet += amt; p.total_bet += amt; t.pot += amt; p.stack = 0; p.all_in = True

        if p.bet > t.street_bet:
            raise_size = p.bet - t.street_bet
            if raise_size >= t.min_raise:
                # Full raise — reopen
                t.min_raise = max(t.min_raise, raise_size)
                for x in t.players:
                    if x is not p and not x.folded and not x.all_in and not x.sitting_out:
                        x.acted = False
            t.street_bet = p.bet

        p.acted = True; p.action = "allin"
        return self._next_turn()

    # ── Turn management ──
    def _next_turn(self):
        t = self.t
        alive = t.alive()
        if len(alive) == 1: return self._win_fold(alive[0])

        can = t.can_act()
        if not can: return self._end_street()
        if all(p.acted and p.bet == t.street_bet for p in can):
            return self._end_street()

        nxt = t._next(t.turn)
        if nxt == -1: return self._end_street()
        t.turn = nxt
        return {"ok": "next"}

    def _end_street(self):
        t = self.t
        # Reset for new street
        for p in t.players: p.bet = 0; p.acted = False
        t.street_bet = 0; t.min_raise = t.bb

        if t.phase == Phase.PREFLOP:
            t.board = [t.deck.pop() for _ in range(3)]; t.phase = Phase.FLOP
        elif t.phase == Phase.FLOP:
            t.board.append(t.deck.pop()); t.phase = Phase.TURN
        elif t.phase == Phase.TURN:
            t.board.append(t.deck.pop()); t.phase = Phase.RIVER
        elif t.phase == Phase.RIVER:
            return self._showdown()

        # If no one can bet, run out
        if not t.can_act():
            self._runout()
            return self._showdown()

        # First to act post-flop: first active after button
        nxt = t._next(t.btn)
        if nxt == -1:
            self._runout()
            return self._showdown()

        t.turn = nxt
        return {"ok": "street", "phase": t.phase.value}

    def _runout(self):
        t = self.t
        while len(t.board) < 5: t.board.append(t.deck.pop())

    def _showdown(self):
        t = self.t; t.phase = Phase.SHOWDOWN; t.turn = -1
        alive = t.alive()
        best = (-1, []); winners = []
        for p in alive:
            sc = best_hand(p.hand + t.board)
            t.results[p.uid] = sc[2]
            if (sc[0], sc[1]) > best:
                best = (sc[0], sc[1]); winners = [p]
            elif (sc[0], sc[1]) == best:
                winners.append(p)
        if winners:
            share = t.pot / len(winners)
            for w in winners: w.stack += share
        t.winners = [w.uid for w in winners]
        # Mark busted
        for p in t.players:
            if p.stack <= 0 and not p.sitting_out: p.sitting_out = True
        return {"ok": "showdown", "winners": t.winners}

    def _win_fold(self, winner):
        t = self.t
        winner.stack += t.pot; t.winners = [winner.uid]
        t.phase = Phase.SHOWDOWN; t.turn = -1
        for p in t.players:
            if p.stack <= 0 and not p.sitting_out: p.sitting_out = True
        return {"ok": "fold_win", "winners": [winner.uid]}

    def next_hand(self):
        return self.deal()

def make_table(sb=1, bb=2, currency="$", max_seats=9):
    tid = uuid.uuid4().hex[:8]
    return Table(id=tid, sb=sb, bb=bb, currency=currency, max_seats=max_seats,
                 min_buyin=bb*10, max_buyin=bb*100), \
           Engine(Table(id=tid, sb=sb, bb=bb, currency=currency, max_seats=max_seats,
                        min_buyin=bb*10, max_buyin=bb*100))

def create_room(sb=1, bb=2, currency="$", max_seats=9):
    tid = uuid.uuid4().hex[:8]
    table = Table(id=tid, sb=sb, bb=bb, currency=currency, max_seats=max_seats,
                  min_buyin=bb*10, max_buyin=bb*100)
    engine = Engine(table)
    return table, engine
