from __future__ import annotations
import random
from typing import Any, Callable
from .base import BaseGame, BaseGameSession, GameInvite, register_game

class BattleshipSession(BaseGameSession):
    """Multiplayer Battleship. 2 players required."""
    
    SHIPS = {
        "carrier": 5,
        "battleship": 4,
        "cruiser": 3,
        "submarine": 3,
        "destroyer": 2
    }
    
    def __init__(self, invite: GameInvite, on_state_change: Callable[[dict[str, Any]], None] | None = None, on_score: Callable[[dict[str, Any]], None] | None = None) -> None:
        super().__init__(invite, on_state_change, on_score)
        self.phase = "setup" # setup, playing, finished
        self.current_turn_idx = 0
        self.players = self.invite.players
        
        self.boards = {}
        self.ships_placed = {}
        self.ships_remaining = {}
        self.ready = {}
        
        for p in self.players:
            self.boards[p] = [[None for _ in range(10)] for _ in range(10)]
            self.ships_placed[p] = []
            self.ships_remaining[p] = len(self.SHIPS)
            self.ready[p] = False
            
        self.last_shot = None
        self._update_state()

    @property
    def current_player(self):
        if not self.players: return ""
        return self.players[self.current_turn_idx % len(self.players)]

    def handle_action(self, player: str, action: str, data: dict[str, Any]) -> None:
        if not self.is_active: return
        
        if self.phase == "setup":
            if action == "place_ship":
                self._handle_place_ship(player, data)
            elif action == "auto_place":
                self._handle_auto_place(player)
            elif action == "ready":
                if len(self.ships_placed[player]) == len(self.SHIPS):
                    self.ready[player] = True
                    if all(self.ready.values()):
                        self.phase = "playing"
                self._update_state()
                self.broadcast_state()
        elif self.phase == "playing":
            if action == "shoot":
                if player != self.current_player: return
                self._handle_shoot(player, data)

    def _handle_place_ship(self, player: str, data: dict[str, Any]):
        ship_name = data.get("ship")
        x = data.get("x", -1)
        y = data.get("y", -1)
        horizontal = data.get("horizontal", True)
        
        if ship_name not in self.SHIPS: return
        if any(s["name"] == ship_name for s in self.ships_placed[player]): return
        
        size = self.SHIPS[ship_name]
        
        # Check bounds
        if horizontal:
            if x < 0 or x + size > 10 or y < 0 or y >= 10: return
        else:
            if x < 0 or x >= 10 or y < 0 or y + size > 10: return
            
        # Check overlap
        board = self.boards[player]
        for i in range(size):
            cx = x + i if horizontal else x
            cy = y if horizontal else y + i
            if board[cy][cx] is not None: return
            
        # Place ship
        cells = []
        for i in range(size):
            cx = x + i if horizontal else x
            cy = y if horizontal else y + i
            board[cy][cx] = "S"
            cells.append({"x": cx, "y": cy, "hit": False})
            
        self.ships_placed[player].append({"name": ship_name, "cells": cells})
        self._update_state()
        self.broadcast_state()

    def _handle_auto_place(self, player: str):
        board = self.boards[player]
        for ship_name, size in self.SHIPS.items():
            if any(s["name"] == ship_name for s in self.ships_placed[player]): continue
            
            placed = False
            while not placed:
                x = random.randint(0, 9)
                y = random.randint(0, 9)
                horizontal = random.choice([True, False])
                
                # Check bounds
                if horizontal:
                    if x + size > 10: continue
                else:
                    if y + size > 10: continue
                    
                # Check overlap
                overlap = False
                for i in range(size):
                    cx = x + i if horizontal else x
                    cy = y if horizontal else y + i
                    if board[cy][cx] is not None:
                        overlap = True
                        break
                if overlap: continue
                
                # Place ship
                cells = []
                for i in range(size):
                    cx = x + i if horizontal else x
                    cy = y if horizontal else y + i
                    board[cy][cx] = "S"
                    cells.append({"x": cx, "y": cy, "hit": False})
                self.ships_placed[player].append({"name": ship_name, "cells": cells})
                placed = True
                
        self.ready[player] = True
        if all(self.ready.values()):
            self.phase = "playing"
        self._update_state()
        self.broadcast_state()

    def _handle_shoot(self, player: str, data: dict[str, Any]):
        x = data.get("x", -1)
        y = data.get("y", -1)
        if not (0 <= x < 10 and 0 <= y < 10): return
        
        opponent = self.players[0] if self.players[1] == player else self.players[1]
        board = self.boards[opponent]
        
        # Already shot here
        if board[y][x] in ["H", "M", "K"]: return
        
        result = "miss"
        if board[y][x] == "S":
            board[y][x] = "H"
            result = "hit"
            
            # Check sunk
            for ship in self.ships_placed[opponent]:
                for cell in ship["cells"]:
                    if cell["x"] == x and cell["y"] == y:
                        cell["hit"] = True
                
                if all(cell["hit"] for cell in ship["cells"]):
                    # Sunk! Mark all cells as K
                    for cell in ship["cells"]:
                        board[cell["y"]][cell["x"]] = "K"
                    # We can't tell which ship was hit directly without checking them all but this works
                    if ship.get("sunk") is not True:
                        ship["sunk"] = True
                        self.ships_remaining[opponent] -= 1
                        result = "sunk"
        else:
            board[y][x] = "M"
            
        self.last_shot = {"x": x, "y": y, "result": result, "by": player}
        
        if self.ships_remaining[opponent] == 0:
            self.phase = "finished"
            self._update_state()
            self.end_game(winner=player)
            return
            
        self.current_turn_idx += 1
        self._update_state()
        self.broadcast_state()

    def get_final_score(self):
        if self.winner:
            return {"winner": self.winner, "draw": False, "players": self.invite.players}
        return {"winner": None, "draw": True, "players": self.invite.players}

    def _update_state(self):
        self.state = {
            "phase": self.phase,
            "current_player": self.current_player,
            "players": self.players,
            "ships_remaining": self.ships_remaining,
            "ready": self.ready,
            "last_shot": self.last_shot,
            "boards": {} # Will be filled in get_render_state securely
        }

    def get_render_state(self):
        # We need to construct the boards securely.
        # But we send both to all clients, relying on the TUI to show the right one.
        # So we send 'own' (full) and 'opponent' (masked) for each player.
        secure_boards = {}
        for p in self.players:
            own = [[cell for cell in row] for row in self.boards[p]]
            
            opponent_mask = []
            for row in self.boards[p]:
                masked_row = []
                for cell in row:
                    if cell == "S":
                        masked_row.append(None)
                    else:
                        masked_row.append(cell)
                opponent_mask.append(masked_row)
                
            secure_boards[p] = {
                "own": own,
                "opponent": opponent_mask
            }
            
        state_copy = dict(self.state)
        state_copy["boards"] = secure_boards
        return {"active": self.is_active, "winner": self.winner, **state_copy}

@register_game
class BattleshipGame(BaseGame):
    game_id = "battleship"
    name = "Battleship"
    description = "Sink the opponent's fleet"
    min_players = 2
    max_players = 2
    is_solo = False
    score_schema = {"wins": "Victoires", "losses": "Défaites"}

    @classmethod
    def create_session(cls, invite, on_state_change=None, on_score=None):
        return BattleshipSession(invite, on_state_change, on_score)
