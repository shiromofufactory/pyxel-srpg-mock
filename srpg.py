import time
import pyxel
from collections import deque

SCREEN_W = 192
SCREEN_H = 312
TILE = 24
VIEW_W = 8    # 192 / 24
VIEW_H = 13   # 312 / 24
MAP_W = 8
MAP_H = 20

PLAIN = 0
FOREST = 1
WATER = 2

SPEAR = 0
CAVALRY = 1
ARCHER = 2

PLAYER = 0
ENEMY = 1

ST_FREE = 0
ST_SELECTED = 1
ST_MOVED = 2
ST_ATTACK = 3
ST_ENEMY = 4
ST_WIN = 5
ST_LOSE = 6

UNIT_NAMES = ["槍兵", "騎兵", "弓兵"]

UNIT_STATS = {
    SPEAR:   {"hp": 30, "atk": 12, "def_": 8,  "mov": 4, "rng": 1},
    CAVALRY: {"hp": 22, "atk": 15, "def_": 5,  "mov": 6, "rng": 1},
    ARCHER:  {"hp": 18, "atk": 10, "def_": 4,  "mov": 4, "rng": 3},
}

MOVE_COST = {
    SPEAR:   [1, 2, 99],
    CAVALRY: [1, 3, 99],
    ARCHER:  [1, 2, 99],
}

FOREST_DEF = 2

TYPE_ADV = {
    (SPEAR, ARCHER): 1.5,
    (CAVALRY, SPEAR): 1.5,
    (ARCHER, CAVALRY): 1.5,
}

ENEMY_INTERVAL = 1
MOVE_ANIM_SPEED = 2
SWIPE_THRESHOLD = 6


def create_map():
    m = [[PLAIN] * MAP_W for _ in range(MAP_H)]

    # Symmetric forests (top-bottom mirror)
    forests = [
        (0, 0, 2, 2),    # top-left corner
        (6, 0, 8, 2),    # top-right corner
        (1, 4, 3, 6),    # upper-left patch
        (5, 4, 7, 6),    # upper-right patch
        (0, 7, 2, 9),    # mid-upper left
        (6, 7, 8, 9),    # mid-upper right
        (0, 11, 2, 13),  # mid-lower left
        (6, 11, 8, 13),  # mid-lower right
        (1, 14, 3, 16),  # lower-left patch
        (5, 14, 7, 16),  # lower-right patch
        (0, 18, 2, 20),  # bottom-left corner
        (6, 18, 8, 20),  # bottom-right corner
    ]
    for x1, y1, x2, y2 in forests:
        for y in range(y1, min(y2, MAP_H)):
            for x in range(x1, min(x2, MAP_W)):
                m[y][x] = FOREST

    # River at y=9-10, ford at x=3-4
    for x in range(MAP_W):
        m[9][x] = WATER
        m[10][x] = WATER
    for x in range(3, 5):
        m[9][x] = PLAIN
        m[10][x] = PLAIN

    return m


class Unit:
    def __init__(self, utype, team, x, y, is_general=False):
        self.type = utype
        self.team = team
        self.x = x
        self.y = y
        self.is_general = is_general
        stats = UNIT_STATS[utype]
        g = 1 if is_general else 0
        self.max_hp = stats["hp"] + g * 10
        self.hp = self.max_hp
        self.atk = stats["atk"] + g * 3
        self.def_ = stats["def_"]
        self.mov = stats["mov"]
        self.rng = stats["rng"]
        self.moved = False
        self.attacked = False
        self.fade_timer = 0

    @property
    def done(self):
        return self.moved and self.attacked

    @property
    def alive(self):
        return self.hp > 0 and self.fade_timer <= 0

    def reset_turn(self):
        self.moved = False
        self.attacked = False


class Game:
    def __init__(self):
        pyxel.init(SCREEN_W, SCREEN_H, title="SRPG モック", fps=30)
        self.font12 = pyxel.Font("umplus_j12r.bdf")
        self.font8 = pyxel.Font("misaki_gothic.bdf")
        self.map_data = create_map()
        self.units = []
        self._setup_units()
        self.cam_y = float(MAP_H - VIEW_H)  # Show player area (bottom)
        self.state = ST_FREE
        self.sel = None
        self.move_cells = set()
        self.atk_cells = set()
        self.pre_move = None
        self.hover_unit = None
        self.cur_tx = 0
        self.cur_ty = 0
        self.turn = 1
        self.enemy_queue = []
        self.enemy_timer = 0
        self.anim_path = []
        self.anim_step = 0
        self.anim_timer = 0
        self.anim_callback = None
        self.move_parents = {}
        self.hover_preview_move = set()
        self.hover_preview_atk = set()
        self._last_hover_unit = None
        self.cursor_atk_preview = set()
        self._last_cursor_cell = None
        self.ctx_menu = None
        self.phase_popup_until = 0.0
        self.phase_popup_text = f"ターン {self.turn}  自フェイズ"
        self.phase_popup_col = 12
        self._first_frame = True
        self.popups = []
        # Touch / swipe state
        self._drag_start = None
        self._drag_cam_start = 0.0
        self._dragging = False
        self._tap_screen_pos = (0, 0)
        pyxel.mouse(False)
        pyxel.run(self.update, self.draw)

    def _setup_units(self):
        psetup = [
            (SPEAR,   3, 17, True),
            (SPEAR,   4, 17, False),
            (CAVALRY, 2, 16, False),
            (CAVALRY, 5, 16, False),
            (ARCHER,  1, 18, False),
            (ARCHER,  6, 18, False),
        ]
        for t, x, y, g in psetup:
            self.units.append(Unit(t, PLAYER, x, y, g))

        esetup = [
            (SPEAR,   4, 2, True),
            (SPEAR,   3, 2, False),
            (CAVALRY, 5, 3, False),
            (CAVALRY, 2, 3, False),
            (ARCHER,  6, 1, False),
            (ARCHER,  1, 1, False),
        ]
        for t, x, y, g in esetup:
            self.units.append(Unit(t, ENEMY, x, y, g))

    # ── Update ────────────────────────────────────────────────────────────────

    def update(self):
        if self._first_frame:
            self._first_frame = False
            self.phase_popup_until = time.time() + 1.5

        # Touch / swipe handling
        just_pressed = pyxel.btnp(pyxel.MOUSE_BUTTON_LEFT)
        pressed = pyxel.btn(pyxel.MOUSE_BUTTON_LEFT)
        just_released = pyxel.btnr(pyxel.MOUSE_BUTTON_LEFT)

        if just_pressed:
            self._drag_start = (pyxel.mouse_x, pyxel.mouse_y)
            self._drag_cam_start = self.cam_y
            self._dragging = False

        if pressed and self._drag_start:
            dy = pyxel.mouse_y - self._drag_start[1]
            if not self._dragging and abs(dy) > SWIPE_THRESHOLD:
                self._dragging = True
            if self._dragging and self.state not in (ST_WIN, ST_LOSE):
                new_cam = self._drag_cam_start + (self._drag_start[1] - pyxel.mouse_y) / TILE
                self.cam_y = max(0.0, min(float(MAP_H - VIEW_H), new_cam))
                self.ctx_menu = None

        tap = False
        if just_released:
            if not self._dragging and self._drag_start:
                tap = True
                tap_mx, tap_my = self._drag_start
                self._tap_screen_pos = (tap_mx, tap_my)
                icy = int(self.cam_y)
                self.cur_tx = max(0, min(MAP_W - 1, tap_mx // TILE))
                self.cur_ty = max(0, min(MAP_H - 1, icy + tap_my // TILE))
                self.hover_unit = next(
                    (u for u in self.units if u.alive and u.x == self.cur_tx and u.y == self.cur_ty),
                    None,
                )
            self._drag_start = None
            self._dragging = False

        # Hover preview: show move/attack range for tapped unit in ST_FREE
        if self.hover_unit and self.state == ST_FREE and self.hover_unit != self.sel:
            if self.hover_unit != self._last_hover_unit:
                self._last_hover_unit = self.hover_unit
                saved_parents = self.move_parents
                hm = self._get_move_range(self.hover_unit)
                self.move_parents = saved_parents
                ha = set()
                for mx2, my2 in hm:
                    ha |= self._get_atk_range_from(self.hover_unit, mx2, my2)
                ha -= hm
                self.hover_preview_move = hm
                self.hover_preview_atk = ha
        else:
            if self._last_hover_unit is not None:
                self._last_hover_unit = None
                self.hover_preview_move = set()
                self.hover_preview_atk = set()

        # Cursor attack preview
        cur_cell = (self.cur_tx, self.cur_ty)
        if self.state == ST_SELECTED and self.sel and cur_cell in self.move_cells:
            if cur_cell != self._last_cursor_cell:
                self._last_cursor_cell = cur_cell
                self.cursor_atk_preview = self._get_atk_range_from(self.sel, cur_cell[0], cur_cell[1])
        else:
            if self._last_cursor_cell is not None:
                self._last_cursor_cell = None
                self.cursor_atk_preview = set()

        # Update popups
        for p in self.popups:
            if p.get("delay", 0) > 0:
                p["delay"] -= 1
            else:
                p["timer"] -= 1
                p["oy"] -= 1.0
        self.popups = [p for p in self.popups if p["timer"] > 0]

        # Update unit fade timers
        for u in self.units:
            if u.fade_timer > 0:
                u.fade_timer -= 1

        # Phase popup blocks all game logic; tap to dismiss
        if time.time() < self.phase_popup_until:
            if tap:
                self.phase_popup_until = 0.0
            return

        if self.anim_path:
            self._upd_anim()
            return

        # Win/Lose: tap to restart
        if self.state in (ST_WIN, ST_LOSE):
            if tap:
                self._restart()
            return

        if self.state == ST_FREE:
            self._upd_free(tap)
        elif self.state == ST_SELECTED:
            self._upd_selected(tap)
        elif self.state == ST_MOVED:
            self._upd_moved(tap)
        elif self.state == ST_ENEMY:
            self._upd_enemy()

    def _upd_free(self, tap):
        if not tap:
            return

        # Context menu handling
        if self.ctx_menu:
            pmx, pmy = self.ctx_menu
            mw, mh = 80, 22
            pmx = min(pmx, SCREEN_W - mw - 2)
            pmy = min(pmy, SCREEN_H - mh - 2)
            tmx, tmy = self._tap_screen_pos
            if pmx <= tmx <= pmx + mw and pmy <= tmy <= pmy + mh:
                self.ctx_menu = None
                self._start_enemy_turn()
                return
            self.ctx_menu = None
            return

        cx, cy = self.cur_tx, self.cur_ty

        # Tap on actionable player unit → select
        u = next(
            (u for u in self.units
             if u.alive and u.team == PLAYER and not u.done
             and u.x == cx and u.y == cy),
            None,
        )
        if u:
            self.sel = u
            self.move_cells = self._get_move_range(u)
            self.atk_cells = set()
            self.hover_preview_move = set()
            self.hover_preview_atk = set()
            self._last_hover_unit = None
            self.state = ST_SELECTED
            return

        # Tap on any unit (enemy/done) → just show info via hover_unit
        any_unit = next(
            (u for u in self.units if u.alive and u.x == cx and u.y == cy),
            None,
        )
        if any_unit:
            return

        # Tap on empty tile → show context menu (ターン終了)
        self.ctx_menu = self._tap_screen_pos

    def _upd_selected(self, tap):
        if not tap:
            return

        cx, cy = self.cur_tx, self.cur_ty

        # Tap on other actionable player unit → switch selection
        other = next(
            (u for u in self.units
             if u.alive and u.team == PLAYER and not u.done
             and u.x == cx and u.y == cy and u != self.sel),
            None,
        )
        if other:
            self.sel = other
            self.move_cells = self._get_move_range(other)
            return

        # Tap on own tile → wait immediately
        if cx == self.sel.x and cy == self.sel.y:
            self.sel.moved = True
            self.sel.attacked = True
            self.move_cells = set()
            self.cursor_atk_preview = set()
            self._last_cursor_cell = None
            self._finish_unit()
            return

        # Tap on enemy in attack range → attack without moving
        cur_atk = self._get_atk_range(self.sel)
        enemy_at = next(
            (u for u in self.units
             if u.alive and u.team == ENEMY and u.x == cx and u.y == cy
             and (cx, cy) in cur_atk),
            None,
        )
        if enemy_at:
            self.sel.moved = True
            self._do_attack(self.sel, enemy_at)
            self.sel.attacked = True
            self.move_cells = set()
            self.cursor_atk_preview = set()
            self._last_cursor_cell = None
            self._check_game_end()
            if self.state not in (ST_WIN, ST_LOSE):
                self._finish_unit()
            return

        # Tap on move cell → move there
        if (cx, cy) in self.move_cells:
            self.pre_move = (self.sel.x, self.sel.y)
            path = self._reconstruct_path((cx, cy))
            self.move_cells = set()
            self.cursor_atk_preview = set()
            self._last_cursor_cell = None
            if len(path) > 1:
                def on_done():
                    self.sel.moved = True
                    self.atk_cells = self._get_targetable_cells(self.sel)
                    self.state = ST_MOVED
                self._start_move_anim(self.sel, path, on_done)
            else:
                self.sel.x, self.sel.y = cx, cy
                self.sel.moved = True
                self.atk_cells = self._get_targetable_cells(self.sel)
                self.state = ST_MOVED
            return

        # Tap on non-movable tile → deselect (right-click equivalent)
        self.sel = None
        self.move_cells = set()
        self.cursor_atk_preview = set()
        self._last_cursor_cell = None
        self.state = ST_FREE

    def _upd_moved(self, tap):
        if not tap:
            return

        cx, cy = self.cur_tx, self.cur_ty

        # Tap on self → wait
        if cx == self.sel.x and cy == self.sel.y:
            self.sel.attacked = True
            self.atk_cells = set()
            self._finish_unit()
            return

        # Tap on enemy in attack range → attack
        if (cx, cy) in self.atk_cells:
            target = next(
                (u for u in self.units
                 if u.alive and u.team == ENEMY and u.x == cx and u.y == cy),
                None,
            )
            if target:
                self._do_attack(self.sel, target)
                self.sel.attacked = True
                self.atk_cells = set()
                self._check_game_end()
                if self.state not in (ST_WIN, ST_LOSE):
                    self._finish_unit()
            return

        # Tap anywhere else → undo move and deselect
        self.sel.x, self.sel.y = self.pre_move
        self.sel.moved = False
        self.pre_move = None
        self.sel = None
        self.atk_cells = set()
        self.move_cells = set()
        self.state = ST_FREE

    def _finish_unit(self):
        self.sel = None
        self.move_cells = set()
        self.atk_cells = set()
        if self.state in (ST_WIN, ST_LOSE):
            return
        alive_players = [u for u in self.units if u.team == PLAYER and u.alive]
        if not alive_players or all(u.done for u in alive_players):
            self._start_enemy_turn()
        else:
            self.state = ST_FREE

    def _restart(self):
        self.units = []
        self._setup_units()
        self.cam_y = float(MAP_H - VIEW_H)
        self.state = ST_FREE
        self.sel = None
        self.move_cells = set()
        self.atk_cells = set()
        self.pre_move = None
        self.hover_unit = None
        self.turn = 1
        self.enemy_queue = []
        self.enemy_timer = 0
        self.anim_path = []
        self.anim_callback = None
        self.move_parents = {}
        self.hover_preview_move = set()
        self.hover_preview_atk = set()
        self._last_hover_unit = None
        self.cursor_atk_preview = set()
        self._last_cursor_cell = None
        self.ctx_menu = None
        self.popups = []
        self.phase_popup_text = f"ターン {self.turn}  自フェイズ"
        self.phase_popup_col = 12
        self.phase_popup_until = time.time() + 1.5

    def _check_game_end(self):
        pg = next((u for u in self.units if u.team == PLAYER and u.is_general and u.alive), None)
        eg = next((u for u in self.units if u.team == ENEMY and u.is_general and u.alive), None)
        if not eg:
            self.state = ST_WIN
        elif not pg:
            self.state = ST_LOSE

    def _start_enemy_turn(self):
        self.state = ST_ENEMY
        self.turn += 1
        for u in self.units:
            if u.team == ENEMY:
                u.reset_turn()
        self.enemy_queue = [u for u in self.units if u.team == ENEMY and u.alive]
        self.enemy_timer = ENEMY_INTERVAL
        self.sel = None
        self.move_cells = set()
        self.atk_cells = set()
        self.ctx_menu = None
        # Snap camera to enemy general
        eg = next((u for u in self.units if u.team == ENEMY and u.is_general and u.alive), None)
        if eg:
            self.cam_y = max(0.0, min(float(MAP_H - VIEW_H), float(eg.y) - VIEW_H / 2))
        self.phase_popup_text = f"ターン {self.turn}  敵フェイズ"
        self.phase_popup_col = 9
        self.phase_popup_until = time.time() + 1.5

    def _start_player_turn(self):
        self.state = ST_FREE
        self.sel = None
        self.move_cells = set()
        self.atk_cells = set()
        self.hover_unit = None
        for u in self.units:
            if u.team == PLAYER:
                u.reset_turn()
        pg = next((u for u in self.units if u.team == PLAYER and u.is_general and u.alive), None)
        if pg:
            self.cam_y = max(0.0, min(float(MAP_H - VIEW_H), float(pg.y) - VIEW_H / 2))
        self.phase_popup_text = f"ターン {self.turn}  自フェイズ"
        self.phase_popup_col = 12
        self.phase_popup_until = time.time() + 1.5

    def _upd_enemy(self):
        self.enemy_timer -= 1

        # Smoothly scroll camera toward next acting unit
        if self.enemy_queue:
            u = self.enemy_queue[0]
            ty = max(0.0, min(float(MAP_H - VIEW_H), float(u.y) - VIEW_H / 2))
            self.cam_y += (ty - self.cam_y) * 0.15

        fading = any(u.fade_timer > 0 for u in self.units)
        if self.enemy_timer > 0 or self.popups or fading:
            return

        if not self.enemy_queue:
            self._start_player_turn()
            return

        unit = self.enemy_queue.pop(0)
        if unit.alive and not unit.done:
            self.cam_y = max(0.0, min(float(MAP_H - VIEW_H), float(unit.y) - VIEW_H / 2))
            self._ai_act(unit)
        else:
            unit.moved = True
            unit.attacked = True
            self._finish_enemy_unit(unit)

    def _ai_act(self, unit):
        players = [u for u in self.units if u.team == PLAYER and u.alive]
        if not players:
            self._finish_enemy_unit(unit)
            return

        general = next((u for u in players if u.is_general), None)
        target = general or min(players, key=lambda u: u.hp)

        move_cells = self._get_move_range(unit)
        best_pos = (unit.x, unit.y)
        best_score = -99999

        for pos in move_cells:
            px, py = pos
            atk = self._get_atk_range_from(unit, px, py)
            can_attack = (target.x, target.y) in atk
            dist = abs(px - target.x) + abs(py - target.y)
            score = (1000 if can_attack else 0) - dist
            if score > best_score:
                best_score = score
                best_pos = pos

        path = self._reconstruct_path(best_pos)

        if len(path) > 1:
            self.sel = unit
            def on_done():
                unit.moved = True
                atk2 = self._get_atk_range(unit)
                targets = [u for u in self.units if u.team == PLAYER and u.alive and (u.x, u.y) in atk2]
                if targets:
                    t = min(targets, key=lambda u: u.hp)
                    self._do_attack(unit, t)
                unit.attacked = True
                self._finish_enemy_unit(unit)
            self._start_move_anim(unit, path, on_done)
        else:
            unit.x, unit.y = best_pos
            unit.moved = True
            atk = self._get_atk_range(unit)
            attackable = [u for u in players if (u.x, u.y) in atk]
            if attackable:
                t = min(attackable, key=lambda u: u.hp)
                self._do_attack(unit, t)
            unit.attacked = True
            self._finish_enemy_unit(unit)

    def _finish_enemy_unit(self, unit):
        self.sel = None
        self._check_game_end()
        if self.state not in (ST_WIN, ST_LOSE):
            self.enemy_timer = ENEMY_INTERVAL

    def _do_attack(self, attacker, defender):
        ATK_PAUSE = 8
        DMG_POPUP_DUR = 12
        COUNTER_DELAY = 10
        # Always snap camera to attacker
        self.cam_y = max(0.0, min(float(MAP_H - VIEW_H), float(attacker.y) - VIEW_H / 2))
        base = max(1, attacker.atk - defender.def_)
        mult = TYPE_ADV.get((attacker.type, defender.type), 1.0)
        tdef = FOREST_DEF if self.map_data[defender.y][defender.x] == FOREST else 0
        dmg = max(1, int((base - tdef) * mult))
        defender.hp = max(0, defender.hp - dmg)
        self.popups.append({"x": defender.x, "y": defender.y, "text": str(dmg),
                            "timer": DMG_POPUP_DUR, "col": 8, "oy": 0,
                            "delay": ATK_PAUSE})
        if defender.hp <= 0:
            defender.fade_timer = 20 + ATK_PAUSE

        if defender.hp > 0:
            crng = self._get_atk_range(defender)
            if (attacker.x, attacker.y) in crng:
                cbase = max(1, defender.atk - attacker.def_)
                cmult = TYPE_ADV.get((defender.type, attacker.type), 1.0)
                tdef2 = FOREST_DEF if self.map_data[attacker.y][attacker.x] == FOREST else 0
                cdmg = max(1, int((cbase - tdef2) * cmult))
                attacker.hp = max(0, attacker.hp - cdmg)
                self.popups.append({"x": attacker.x, "y": attacker.y, "text": str(cdmg),
                                    "timer": DMG_POPUP_DUR, "col": 8, "oy": 0,
                                    "delay": ATK_PAUSE + COUNTER_DELAY})
                if attacker.hp <= 0:
                    attacker.fade_timer = 20 + ATK_PAUSE + COUNTER_DELAY

    # ── Pathfinding ───────────────────────────────────────────────────────────

    def _get_move_range(self, unit):
        start = (unit.x, unit.y)
        best = {start: unit.mov}
        parent = {start: None}
        queue = deque([(unit.x, unit.y, unit.mov)])
        enemy_pos = {(u.x, u.y) for u in self.units if u.team != unit.team and u.alive}
        ally_pos = {(u.x, u.y) for u in self.units
                    if u.team == unit.team and u.alive and u != unit}

        while queue:
            x, y, rem = queue.popleft()
            if best.get((x, y), -1) > rem:
                continue
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nx, ny = x + dx, y + dy
                if not (0 <= nx < MAP_W and 0 <= ny < MAP_H):
                    continue
                cost = MOVE_COST[unit.type][self.map_data[ny][nx]]
                if cost >= 99 or (nx, ny) in enemy_pos:
                    continue
                nr = rem - cost
                if nr < 0:
                    continue
                if best.get((nx, ny), -1) < nr:
                    best[(nx, ny)] = nr
                    parent[(nx, ny)] = (x, y)
                    if not self._is_zoc(nx, ny, unit.team):
                        queue.append((nx, ny, nr))

        self.move_parents = parent
        return {pos for pos in best if pos not in ally_pos}

    def _is_zoc(self, x, y, team):
        et = 1 - team
        return any(
            u for u in self.units
            if u.team == et and u.alive and abs(u.x - x) + abs(u.y - y) == 1
        )

    def _get_atk_range(self, unit, from_pos=None):
        x, y = from_pos if from_pos else (unit.x, unit.y)
        return self._get_atk_range_from(unit, x, y)

    def _get_atk_range_from(self, unit, x, y):
        cells = set()
        r = unit.rng
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if 1 <= abs(dx) + abs(dy) <= r:
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < MAP_W and 0 <= ny < MAP_H:
                        cells.add((nx, ny))
        return cells

    def _get_targetable_cells(self, unit):
        atk = self._get_atk_range(unit)
        enemy_pos = {(u.x, u.y) for u in self.units if u.team != unit.team and u.alive}
        return atk & enemy_pos

    # ── Move animation ────────────────────────────────────────────────────────

    def _reconstruct_path(self, dest):
        path = []
        pos = dest
        while pos is not None:
            path.append(pos)
            pos = self.move_parents.get(pos)
        path.reverse()
        return path

    def _start_move_anim(self, unit, path, callback):
        self.anim_path = path
        self.anim_step = 0
        self.anim_timer = 0
        self.anim_callback = callback
        unit.x, unit.y = path[0]

    def _upd_anim(self):
        self.anim_timer += 1
        if self.anim_timer >= MOVE_ANIM_SPEED:
            self.anim_timer = 0
            self.anim_step += 1
            if self.anim_step >= len(self.anim_path):
                dest = self.anim_path[-1]
                unit = self.sel
                unit.x, unit.y = dest
                cb = self.anim_callback
                self.anim_path = []
                self.anim_callback = None
                cb()
                return
            self.sel.x, self.sel.y = self.anim_path[self.anim_step]

    # ── Draw ──────────────────────────────────────────────────────────────────

    def draw(self):
        pyxel.cls(0)
        cx, cy = 0, int(self.cam_y)
        self._draw_map(cx, cy)
        self._draw_highlights(cx, cy)
        self._draw_units(cx, cy)
        self._draw_cursor(cx, cy)
        self._draw_ui(cx, cy)
        self._draw_popups(cx, cy)

    def _draw_map(self, cx, cy):
        for ty in range(VIEW_H + 1):
            for tx in range(VIEW_W + 1):
                mx, my = tx + cx, ty + cy
                if not (0 <= mx < MAP_W and 0 <= my < MAP_H):
                    continue
                terrain = self.map_data[my][mx]
                sx, sy = tx * TILE, ty * TILE

                if terrain == PLAIN:
                    pyxel.rect(sx, sy, TILE, TILE, 3)
                elif terrain == FOREST:
                    pyxel.rect(sx, sy, TILE, TILE, 3)
                    pyxel.tri(sx + 12, sy + 3, sx + 4, sy + 16, sx + 20, sy + 16, 11)
                    pyxel.rect(sx + 10, sy + 16, 4, 6, 4)
                elif terrain == WATER:
                    pyxel.rect(sx, sy, TILE, TILE, 1)
                    pyxel.line(sx + 2, sy + 7, sx + 8, sy + 4, 12)
                    pyxel.line(sx + 8, sy + 4, sx + 14, sy + 7, 12)
                    pyxel.line(sx + 14, sy + 7, sx + 21, sy + 4, 12)
                    pyxel.line(sx + 3, sy + 15, sx + 9, sy + 12, 6)
                    pyxel.line(sx + 9, sy + 12, sx + 15, sy + 15, 6)

    def _draw_highlights(self, cx, cy):
        for mx, my in self.hover_preview_move:
            sx = (mx - cx) * TILE
            sy = (my - cy) * TILE
            if -TILE < sx < SCREEN_W and -TILE < sy < SCREEN_H:
                pyxel.dither(0.3)
                pyxel.rect(sx, sy, TILE, TILE, 6)
                pyxel.dither(1.0)

        for mx, my in self.hover_preview_atk:
            sx = (mx - cx) * TILE
            sy = (my - cy) * TILE
            if -TILE < sx < SCREEN_W and -TILE < sy < SCREEN_H:
                pyxel.dither(0.3)
                pyxel.rect(sx, sy, TILE, TILE, 8)
                pyxel.dither(1.0)

        for mx, my in self.move_cells:
            sx = (mx - cx) * TILE
            sy = (my - cy) * TILE
            if -TILE < sx < SCREEN_W and -TILE < sy < SCREEN_H:
                pyxel.dither(0.5)
                pyxel.rect(sx, sy, TILE, TILE, 6)
                pyxel.dither(1.0)
                pyxel.rectb(sx, sy, TILE, TILE, 12)

        for mx, my in self.cursor_atk_preview:
            sx = (mx - cx) * TILE
            sy = (my - cy) * TILE
            if -TILE < sx < SCREEN_W and -TILE < sy < SCREEN_H:
                pyxel.dither(0.3)
                pyxel.rect(sx, sy, TILE, TILE, 8)
                pyxel.dither(1.0)

        for mx, my in self.atk_cells:
            sx = (mx - cx) * TILE
            sy = (my - cy) * TILE
            if -TILE < sx < SCREEN_W and -TILE < sy < SCREEN_H:
                pyxel.dither(0.5)
                pyxel.rect(sx, sy, TILE, TILE, 8)
                pyxel.dither(1.0)
                pyxel.rectb(sx, sy, TILE, TILE, 9)

        if self.anim_path:
            for i, (mx, my) in enumerate(self.anim_path):
                sx = (mx - cx) * TILE
                sy = (my - cy) * TILE
                if not (-TILE < sx < SCREEN_W and -TILE < sy < SCREEN_H):
                    continue
                if i <= self.anim_step:
                    pyxel.dither(0.4)
                    pyxel.rect(sx, sy, TILE, TILE, 10)
                    pyxel.dither(1.0)
                else:
                    pyxel.dither(0.25)
                    pyxel.rect(sx, sy, TILE, TILE, 6)
                    pyxel.dither(1.0)
                pyxel.circ(sx + TILE // 2, sy + TILE // 2, 3, 10 if i <= self.anim_step else 6)

    def _draw_units(self, cx, cy):
        for u in self.units:
            if not u.alive and u.fade_timer <= 0:
                continue
            sx = (u.x - cx) * TILE
            sy = (u.y - cy) * TILE
            if not (-TILE < sx < SCREEN_W and -TILE < sy < SCREEN_H):
                continue

            fading = u.fade_timer > 0
            if fading:
                pyxel.dither(u.fade_timer / 20.0)

            is_player_turn = self.state in (ST_FREE, ST_SELECTED, ST_MOVED, ST_ATTACK)
            show_done = u.done and ((u.team == PLAYER and is_player_turn) or (u.team == ENEMY and not is_player_turn))
            if show_done:
                bg = 13
            elif u.team == PLAYER:
                bg = 5
            else:
                bg = 2

            pyxel.rect(sx + 2, sy + 2, TILE - 4, TILE - 4, bg)

            char = ["槍", "騎", "弓"][u.type]
            if show_done:
                tcol = 7
            elif u.team == PLAYER:
                tcol = 12
            else:
                tcol = 9
            pyxel.text(sx + 6, sy + 4, char, tcol, self.font12)

            if u.is_general:
                pyxel.text(sx + TILE - 8, sy + 1, "*", 10)

            if fading:
                pyxel.dither(1.0)

            # Selected unit highlight
            if u == self.sel:
                if self.state != ST_MOVED or pyxel.frame_count % 20 < 14:
                    pyxel.rectb(sx, sy, TILE, TILE, 10)
                    pyxel.rectb(sx + 1, sy + 1, TILE - 2, TILE - 2, 10)

            # HP bar
            bw = TILE - 6
            ratio = u.hp / u.max_hp
            filled = max(0, int(bw * ratio))
            pyxel.rect(sx + 3, sy + TILE - 5, bw, 3, 0)
            hcol = 11 if ratio > 0.6 else (10 if ratio > 0.3 else 8)
            if filled > 0:
                pyxel.rect(sx + 3, sy + TILE - 5, filled, 3, hcol)

    def _draw_cursor(self, cx, cy):
        if self.state in (ST_WIN, ST_LOSE, ST_ENEMY):
            return
        sx = (self.cur_tx - cx) * TILE
        sy = (self.cur_ty - cy) * TILE
        if 0 <= sx < SCREEN_W and 0 <= sy < SCREEN_H:
            pyxel.rectb(sx, sy, TILE, TILE, 10)
            pyxel.rectb(sx + 1, sy + 1, TILE - 2, TILE - 2, 10)

    def _draw_ui(self, cx, cy):
        # Unit info popup
        if self.hover_unit and self.state not in (ST_WIN, ST_LOSE) and not self.anim_path:
            self._draw_hover_info(self.hover_unit, cx, cy)

        # Context menu (ターン終了)
        if self.ctx_menu and self.state == ST_FREE:
            pmx, pmy = self.ctx_menu
            mw, mh = 80, 22
            pmx = min(pmx, SCREEN_W - mw - 2)
            pmy = min(pmy, SCREEN_H - mh - 2)
            pyxel.rect(pmx, pmy, mw, mh, 1)
            pyxel.rectb(pmx, pmy, mw, mh, 13)
            pyxel.text(pmx + 6, pmy + 5, "ターン終了", 7, self.font12)

        # Phase popup
        if time.time() < self.phase_popup_until:
            w, h = 180, 40
            x = (SCREEN_W - w) // 2
            y = (SCREEN_H - h) // 2
            pyxel.rect(x - 2, y - 2, w + 4, h + 4, self.phase_popup_col)
            pyxel.rect(x, y, w, h, 0)
            pyxel.rectb(x, y, w, h, self.phase_popup_col)
            tw = self.font12.text_width(self.phase_popup_text)
            pyxel.text((SCREEN_W - tw) // 2, y + 14, self.phase_popup_text,
                       self.phase_popup_col, self.font12)

        # Win / Lose overlay
        if self.state == ST_WIN:
            self._draw_result("味方の勝利！", 12)
        elif self.state == ST_LOSE:
            self._draw_result("味方の敗北...", 9)

    def _draw_popups(self, cx, cy):
        for p in self.popups:
            if p.get("delay", 0) > 0:
                continue
            sx = (p["x"] - cx) * TILE + TILE // 2
            sy = (p["y"] - cy) * TILE + int(p["oy"])
            tw = len(p["text"]) * pyxel.FONT_WIDTH
            px = sx - tw // 2
            pyxel.text(px + 1, sy + 1, p["text"], 0)
            pyxel.text(px, sy, p["text"], p["col"])

    def _draw_hover_info(self, u, cx, cy):
        sy_unit = (u.y - cy) * TILE + TILE // 2

        pw, ph = SCREEN_W - 8, 82
        px = 4
        if sy_unit < SCREEN_H // 2:
            py = SCREEN_H - ph - 4
        else:
            py = 4

        pyxel.rect(px, py, pw, ph, 1)
        pyxel.rectb(px, py, pw, ph, 13)

        team_s = "自軍" if u.team == PLAYER else "敵軍"
        gen_s = "（大将）" if u.is_general else ""
        name_s = UNIT_NAMES[u.type] + gen_s
        tcol = 12 if u.team == PLAYER else 9
        pyxel.text(px + 3, py + 3, team_s + " " + name_s, tcol, self.font12)

        hp_s = f"HP {u.hp}/{u.max_hp}"
        pyxel.text(px + 3, py + 17, hp_s, 7, self.font12)

        bx2 = px + 3
        by2 = py + 31
        bw2 = pw - 8
        pyxel.rect(bx2, by2, bw2, 4, 0)
        ratio = u.hp / u.max_hp
        hcol = 11 if ratio > 0.6 else (10 if ratio > 0.3 else 8)
        pyxel.rect(bx2, by2, int(bw2 * ratio), 4, hcol)

        pyxel.text(px + 3, py + 38, f"攻{u.atk} 防{u.def_} 移{u.mov} 射{u.rng}", 7, self.font12)

        tnames = ["平地", "森", "川・海"]
        pyxel.text(px + 3, py + 52, "地形:" + tnames[self.map_data[u.y][u.x]], 7, self.font12)

        adv = ["弓兵", "槍兵", "騎兵"][u.type]
        weak = ["騎兵", "弓兵", "槍兵"][u.type]
        pyxel.text(px + 3, py + 66, "強:" + adv + " 弱:" + weak, 7, self.font12)

    def _draw_result(self, msg, col):
        w, h = 180, 58
        x = (SCREEN_W - w) // 2
        y = (SCREEN_H - h) // 2
        pyxel.rect(x, y, w, h, 0)
        pyxel.rectb(x, y, w, h, col)
        tw = self.font12.text_width(msg)
        pyxel.text((SCREEN_W - tw) // 2, y + 12, msg, col, self.font12)
        restart = "タップでリスタート"
        rw = self.font12.text_width(restart)
        if pyxel.frame_count % 40 < 28:
            pyxel.text((SCREEN_W - rw) // 2, y + 36, restart, 13, self.font12)


Game()
