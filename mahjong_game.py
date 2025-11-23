import random
import eventlet
from flask_socketio import SocketIO
from constants import ALL_TILES, TILE_MAP, REVERSE_TILE_MAP, DEFAULT_SPECIAL_HANDS_CONFIG
import threading

class MahjongGame:
    def __init__(self, room_id, special_hands_config=None):
        self.room_id = room_id
        self.player_ids = []
        self.player_hands = {} # {player_id: [tiles]}
        self.discard_piles = {} # {player_id: [tiles]}
        self.melds = {} # {player_id: [[meld_type, tiles]]}
        self.wall = []
        self.current_dealer_id = None
        self.current_turn_player_id = None
        self.last_discarded_tile = None
        self.game_started = False
        self.waiting_for_action = False
        self.action_options = {} # {player_id: {'hu': bool, 'peng': bool, 'gang': bool}}
        self.action_timer = None
        self.turn_history = []
        # Use provided config or default config for special hands
        self.special_hands_config = special_hands_config if special_hands_config is not None else DEFAULT_SPECIAL_HANDS_CONFIG
        
        # Add a lock to handle concurrent operations
        self.game_lock = threading.Lock()

    def add_player(self, player_id):
        if len(self.player_ids) < 4:
            self.player_ids.append(player_id)
            self.player_hands[player_id] = []
            self.discard_piles[player_id] = []
            self.melds[player_id] = []
            print(f"Player {player_id} added to room {self.room_id}")
            return True
        return False

    def start_game(self):
        if len(self.player_ids) == 4 and not self.game_started:
            print(f"Starting game in room {self.room_id} with players: {self.player_ids}")
            from server import socketio
            socketio.emit('message', {'data': 'Shuffling tiles...'}, room=self.room_id)
            self._initialize_wall()
            self._deal_tiles()
            self.current_dealer_id = self.player_ids[0]
            self.current_turn_player_id = self.current_dealer_id
            self.game_started = True
            self.broadcast_game_state()
            dealer_sid = self._get_player_sid(self.current_dealer_id)
            socketio.emit('your_turn_to_discard', {}, room=dealer_sid)
            return True
        return False

    def _initialize_wall(self):
        self.wall = list(ALL_TILES)
        random.shuffle(self.wall)
        print(f"Wall initialized with {len(self.wall)} tiles.")

    def _deal_tiles(self):
        for i, player_id in enumerate(self.player_ids):
            # Dealer gets 14, others get 13
            num_tiles = 14 if i == 0 else 13
            for _ in range(num_tiles):
                if self.wall:
                    tile = self.wall.pop(0)
                    self.player_hands[player_id].append(tile)
            self.player_hands[player_id].sort() # Keep hands sorted for easier display and logic
            print(f"Dealt {len(self.player_hands[player_id])} tiles to {player_id}: {self.player_hands[player_id]}")

    def _draw_initial_tile_for_dealer(self):
        # After dealing, dealer needs to discard one tile to start the game
        # This is handled in the client by allowing the dealer to discard one of their 14 tiles
        # The actual 'draw' action will come after the first discard
        print(f"Dealer {self.current_dealer} needs to discard a tile to start.")

    def _check_kong_after_draw(self, player_id, drawn_tile):
        """
        Check if the player can perform a Kong (gang) after drawing a tile.
        This could be an Ankan (暗槓, concealed kong) or Kakan (加槓, added kong).
        """
        hand = self.player_hands[player_id]
        actions = {'kong_self': False, 'melds': []}
        
        # Check for Ankan (4 identical tiles in hand + drawn tile = 4 identical tiles)
        if hand.count(drawn_tile) == 3:
            actions['kong_self'] = True
            actions['melds'].append(['ankan', [drawn_tile] * 4])
        
        # Check for Kakan (add to existing Pong to make Kong)
        for meld_type, meld_tiles in self.melds[player_id]:
            if meld_type == 'peng' and len(meld_tiles) == 3 and meld_tiles[0] == drawn_tile:
                actions['kong_self'] = True
                actions['melds'].append(['kakan', [drawn_tile] * 4])
        
        return actions

    def draw_tile(self, player_id):
        if not self.wall:
            print("No tiles left in wall.")
            # Handle game draw
            return None
        tile = self.wall.pop(0)
        self.player_hands[player_id].append(tile)
        self.player_hands[player_id].sort()
        
        # Check if player can kong after drawing
        kong_options = self._check_kong_after_draw(player_id, tile)
        if kong_options['kong_self']:
            # Notify player about kong option
            player_sid = self._get_player_sid(player_id)
            if player_sid:
                from server import socketio
                socketio.emit('kong_option', {
                    'tile': tile, 
                    'kong_types': [meld[0] for meld in kong_options['melds']]
                }, room=player_sid)
        
        player_sid = self._get_player_sid(player_id)
        if player_sid:
            from server import socketio
            socketio.emit('tile_drawn', {'tile': tile}, room=player_sid)
        
        print(f"Player {player_id} drew {tile}. Hand: {self.player_hands[player_id]}")
        return tile

    def discard_tile(self, player_id, tile):
        # Acquire lock to prevent race conditions
        with self.game_lock:
            if player_id != self.current_turn_player_id:
                print(f"Not {player_id}'s turn to discard.")
                return False
            if tile not in self.player_hands[player_id]:
                print(f"Player {player_id} does not have tile {tile} to discard.")
                return False

            self.player_hands[player_id].remove(tile)
            self.discard_piles[player_id].append(tile)
            self.last_discarded_tile = tile
            print(f"Player {player_id} discarded {tile}. Hand: {self.player_hands[player_id]}")

            self.turn_history.append({'player': player_id, 'action': 'discard', 'tile': tile})
            self.broadcast_game_state()
            self.check_for_actions(tile, player_id) # Check for Pong/Gang/Hu from other players
            return True

    def _is_winning_hand(self, hand_tiles):
        """
        Checks if a given hand (as a list of numeric tiles) forms a winning hand (4 sets + 1 pair).
        This function handles standard winning conditions.
        It's a recursive backtracking algorithm.
        """
        numeric_hand = self._to_numeric_tiles(hand_tiles)
        tile_counts = self._count_tiles(numeric_hand)

        # A winning hand must have a total of 14 tiles (after drawing the winning tile)
        # However, this function is typically called with a 14-tile hand.
        # If it's called with 13, it means we are checking for tenpai on a 13-tile hand.
        if sum(tile_counts.values()) % 3 != 2: # A winning hand must have 2 tiles left after forming melds (the pair)
            return False # Not a 4 sets + 1 pair structure if divisible by 3

        # Base case: if all tiles are used, and we found a pair, it's a winning hand.
        def solve(current_counts, pairs_found, sets_found):
            if not any(current_counts.values()):
                return sets_found == 4 and pairs_found == 1

            # Get the smallest tile in the hand
            tiles_in_hand = sorted([tile for tile, count in current_counts.items() if count > 0])
            if not tiles_in_hand:
                return sets_found == 4 and pairs_found == 1
            
            tile = tiles_in_hand[0]
            suit = tile[0]
            rank = int(tile[1:]) if len(tile) > 1 else 0

            # Option 1: Try to form a pair (Jantou)
            if pairs_found == 0 and current_counts.get(tile, 0) >= 2:
                new_counts = dict(current_counts)
                new_counts[tile] -= 2
                if solve(new_counts, 1, sets_found):
                    return True

            # Option 2: Try to form a triplet (Kōtsu)
            if current_counts.get(tile, 0) >= 3:
                new_counts = dict(current_counts)
                new_counts[tile] -= 3
                if solve(new_counts, pairs_found, sets_found + 1):
                    return True

            # Option 3: Try to form a sequence (Shuntsu) - only for numbered tiles
            if suit in ['m', 'p', 's'] and rank <= 7:
                if current_counts.get(tile, 0) >= 1 and \
                   current_counts.get(f"{suit}{rank+1}", 0) >= 1 and \
                   current_counts.get(f"{suit}{rank+2}", 0) >= 1:
                    new_counts = dict(current_counts)
                    new_counts[tile] -= 1
                    new_counts[f"{suit}{rank+1}"] -= 1
                    new_counts[f"{suit}{rank+2}"] -= 1
                    if solve(new_counts, pairs_found, sets_found + 1):
                        return True
            
            # If the first tile can't form any combination, it must be part of another branch
            # or the current path is not valid.
            # This is key for the recursive backtracking. We *must* consume the current tile
            # to make progress, either as part of a set/sequence/pair, or as an unmatchable tile
            # which indicates this path is invalid.
            # This means if no sets/sequences/pairs can be formed with the current tile,
            # this 'solve' branch will simply return False, leading to backtracking.
            
            # The key for backtracking is that we MUST try to use the 'tile' in `tiles_in_hand[0]`
            # in some way. If we can't form a pair, triplet, or sequence with it, then this
            # specific branch of the recursion (starting from current_counts) is invalid.
            return False

        # Iterate through each possible pair (jantou) in the hand to start the recursion
        for tile in sorted(tile_counts.keys()):
            if tile_counts.get(tile, 0) >= 2:
                temp_counts = dict(tile_counts)
                temp_counts[tile] -= 2
                if solve(temp_counts, 1, 0): # Start recursion with 1 pair found, 0 sets found
                    return True
        return False

    def _action_timeout(self):
        """Callback for action timer if no player responds."""
        from server import app
        with app.app_context():
            if self.action_options: # If there are still pending actions
                print("Action timeout: No players responded. Proceeding to next turn.")
                # Reset action state
                self.waiting_for_action = False
                self.action_options = {}
                self.action_timer = None
                self.next_turn()
            
    def _can_chi(self, hand, discarded_tile, dis_player_id, player_id):
        """
        Check if a player can chi (eat) a discarded tile.
        Chi requires the player to have two tiles that form a sequence with the discarded tile.
        Only numbered tiles (manzu, pinzu, souzu) can be chii'd.
        Chi can only be performed on the player immediately before the current player in turn order.
        """
        # Check if the discarded tile is a character, circle, or bamboo tile (numbered tiles)
        tile_numeric = self._tile_map.get(discarded_tile)
        if not tile_numeric or tile_numeric[0] not in ['m', 'p', 's']:
            return False
        
        # Chi can only be performed on the player immediately before the current player in turn order
        # This means if player A discards, player B (next in turn) can chi
        # So we check if the player_id parameter (the player we're checking) is the next player after dis_player_id
        discarder_index = self.player_ids.index(dis_player_id)
        next_player_index = (discarder_index + 1) % 4
        next_player_id = self.player_ids[next_player_index]
        
        # Only allow chi if the player we're checking is the one after the discarder
        if player_id != next_player_id:
            return False

        # Convert hand to numeric for easier processing
        numeric_hand = self._to_numeric_tiles(hand)
        
        # Find all possible sequences that include the discarded tile
        suit = tile_numeric[0]
        rank = int(tile_numeric[1:])
        
        # Check for possible chi combinations:
        # 1. (n-2, n-1, discarded) - e.g., 1,2,3 if discarded is 3
        # 2. (n-1, discarded, n+1) - e.g., 2,3,4 if discarded is 3
        # 3. (discarded, n+1, n+2) - e.g., 3,4,5 if discarded is 3
        
        # Check for (n-2, n-1, discarded)
        if rank >= 3:
            tile1 = f"{suit}{rank-2}"
            tile2 = f"{suit}{rank-1}"
            if numeric_hand.count(tile1) > 0 and numeric_hand.count(tile2) > 0:
                return True
        
        # Check for (n-1, discarded, n+1)
        if 2 <= rank <= 8:
            tile1 = f"{suit}{rank-1}"
            tile2 = f"{suit}{rank+1}"
            if numeric_hand.count(tile1) > 0 and numeric_hand.count(tile2) > 0:
                return True
                
        # Check for (discarded, n+1, n+2)
        if rank <= 7:
            tile1 = f"{suit}{rank+1}"
            tile2 = f"{suit}{rank+2}"
            if numeric_hand.count(tile1) > 0 and numeric_hand.count(tile2) > 0:
                return True
        
        return False

    def check_for_actions(self, discarded_tile, dis_player_id):
        # Acquire lock to prevent race conditions
        with self.game_lock:
            self.waiting_for_action = False
            potential_actions = {}
            for player_id in self.player_ids:
                if player_id == dis_player_id: continue  # Cannot chi from yourself
                hand = self.player_hands[player_id]
                actions = {'hu': False, 'peng': False, 'gang': False, 'chi': False}
                
                # Check for Hu (winning)
                if self._is_winning_hand(hand + [discarded_tile]): 
                    actions['hu'] = True
                
                # Check for Gang (kong)
                if hand.count(discarded_tile) == 3: 
                    actions['gang'] = True
                
                # Check for Peng (pong)
                if hand.count(discarded_tile) >= 2: 
                    actions['peng'] = True
                
                # Check for Chi (chow) - only for numbered tiles (manzu, pinzu, souzu)
                if self._can_chi(hand, discarded_tile, dis_player_id, player_id):
                    actions['chi'] = True
                
                if any(actions.values()):
                    potential_actions[player_id] = actions
                    self.waiting_for_action = True
                    player_sid = self._get_player_sid(player_id)
                    if player_sid: # Check if player is connected
                        from server import socketio
                        socketio.emit('action_option', {'options': actions, 'discarded_tile': discarded_tile}, room=player_sid)
            
            if not self.waiting_for_action:
                self.next_turn()
            else:
                self.action_options = potential_actions
                if self.action_timer: self.action_timer.cancel()
                self.action_timer = eventlet.spawn_after(10, self._action_timeout)
                print(f"Waiting for actions from {list(potential_actions.keys())} for 10 seconds.")

    def perform_action(self, player_id, action_type):
        # Acquire lock to prevent race conditions
        with self.game_lock:
            if player_id not in self.action_options or not self.waiting_for_action:
                print(f"Player {player_id} cannot perform action {action_type} at this time.")
                return False

        # If a player performs an action, cancel the timeout
        if self.action_timer:
            self.action_timer.cancel()
            self.action_timer = None
            print("Action timer cancelled due to player action.")

        discarded_tile = self.last_discarded_tile
        if not discarded_tile:
            print("No tile has been discarded to perform an action on.")
            return False
            
        if action_type == 'pass':
            del self.action_options[player_id]
            from server import socketio, players
            socketio.emit('message', {'data': f"Player {players[player_id]['username']} passed."}, room=self.room_id)
            if not self.action_options:
                self.waiting_for_action = False
                self.next_turn()
            return True

        # For Hu, Gang, Peng, Chi, the turn shifts.
        discarder_id = self.current_turn_player_id

        if action_type == 'hu' and self.action_options[player_id].get('hu'):
            socketio.emit('message', {'data': f"Player {players[player_id]['username']} DECLARED HU!"}, room=self.room_id)
            self.game_started = False
            self.broadcast_game_state()
            return True

        if action_type == 'gang' and self.action_options[player_id].get('gang'):
            self.player_hands[player_id] = [t for t in self.player_hands[player_id] if t != discarded_tile]
            self.melds[player_id].append(['gang', [discarded_tile] * 4])
            if self.discard_piles[discarder_id]: self.discard_piles[discarder_id].pop()
            self.current_turn_player_id = player_id
            self.waiting_for_action = False
            self.action_options = {}
            new_tile = self.draw_tile_from_rinshan() # Draw from dead wall
            socketio.emit('message', {'data': f"Player {players[player_id]['username']} GANGED {discarded_tile}!"}, room=self.room_id)
            if new_tile:
                player_sid = self._get_player_sid(player_id)
                if player_sid:
                    socketio.emit('tile_drawn', {'tile': new_tile}, room=player_sid) # Emit drawn tile to the player
            self.broadcast_game_state()
            player_sid = self._get_player_sid(player_id)
            if player_sid: socketio.emit('your_turn_to_discard', {}, room=player_sid)
            return True

        if action_type == 'peng' and self.action_options[player_id].get('peng'):
            for _ in range(2): self.player_hands[player_id].remove(discarded_tile)
            self.melds[player_id].append(['peng', [discarded_tile] * 3])
            if self.discard_piles[discarder_id]: self.discard_piles[discarder_id].pop()
            self.current_turn_player_id = player_id
            self.waiting_for_action = False
            self.action_options = {}
            socketio.emit('message', {'data': f"Player {players[player_id]['username']} PENGED {discarded_tile}!"}, room=self.room_id)
            self.broadcast_game_state()
            player_sid = self._get_player_sid(player_id)
            if player_sid: socketio.emit('your_turn_to_discard', {}, room=player_sid)
            return True

        if action_type == 'chi' and self.action_options[player_id].get('chi'):
            # Find the tiles needed to form the chi sequence with the discarded tile
            numeric_hand = self._to_numeric_tiles(self.player_hands[player_id])
            tile_numeric = self._tile_map.get(discarded_tile)
            if not tile_numeric or tile_numeric[0] not in ['m', 'p', 's']:
                return False  # Should not happen if _can_chi returned True
            
            suit = tile_numeric[0]
            rank = int(tile_numeric[1:])
            tiles_to_remove = []
            
            # Find the two tiles that form the sequence with the discarded tile
            # Check for (n-2, n-1, discarded) - e.g., 1,2,3 if discarded is 3
            if rank >= 3:
                tile1 = f"{suit}{rank-2}"
                tile2 = f"{suit}{rank-1}"
                if numeric_hand.count(tile1) > 0 and numeric_hand.count(tile2) > 0:
                    tiles_to_remove = [tile1, tile2]
            
            # Check for (n-1, discarded, n+1) - e.g., 2,3,4 if discarded is 3
            if not tiles_to_remove and 2 <= rank <= 8:
                tile1 = f"{suit}{rank-1}"
                tile2 = f"{suit}{rank+1}"
                if numeric_hand.count(tile1) > 0 and numeric_hand.count(tile2) > 0:
                    tiles_to_remove = [tile1, tile2]
            
            # Check for (discarded, n+1, n+2) - e.g., 3,4,5 if discarded is 3
            if not tiles_to_remove and rank <= 7:
                tile1 = f"{suit}{rank+1}"
                tile2 = f"{suit}{rank+2}"
                if numeric_hand.count(tile1) > 0 and numeric_hand.count(tile2) > 0:
                    tiles_to_remove = [tile1, tile2]
            
            if not tiles_to_remove:
                return False  # Should not happen if _can_chi returned True
            
            # Convert numeric tiles back to actual tiles and remove from hand
            tiles_to_remove_actual = []
            for num_tile in tiles_to_remove:
                # Find the corresponding actual tile in the player's hand
                # Make sure to match by numeric value and count occurrences properly
                for actual_tile in self.player_hands[player_id]:
                    if self._tile_map.get(actual_tile) == num_tile:
                        # Check if this tile is not already in the list to avoid duplicates
                        if actual_tile not in tiles_to_remove_actual:
                            tiles_to_remove_actual.append(actual_tile)
                            break
            
            # Remove the tiles from the player's hand
            for tile in tiles_to_remove_actual:
                if tile in self.player_hands[player_id]:
                    self.player_hands[player_id].remove(tile)
            
            # Create the chi meld (sort the tiles in the meld)
            chi_tiles = sorted([discarded_tile] + tiles_to_remove_actual, key=lambda x: self._tile_map.get(x, ''))
            self.melds[player_id].append(['chi', chi_tiles])
            
            # Remove the discarded tile from the discard pile of the discarder
            if self.discard_piles[discarder_id]: self.discard_piles[discarder_id].pop()
            
            # Set the current turn to the player who chi'd
            self.current_turn_player_id = player_id
            self.waiting_for_action = False
            self.action_options = {}
            
            socketio.emit('message', {'data': f"Player {players[player_id]['username']} CHI'D {', '.join(chi_tiles)}!"}, room=self.room_id)
            self.broadcast_game_state()
            player_sid = self._get_player_sid(player_id)
            if player_sid: socketio.emit('your_turn_to_discard', {}, room=player_sid)
            return True

        # Handle self-draw kong (ankan or kakan)
        if action_type == 'ankan' or action_type == 'kakan':
            if action_type == 'ankan':
                # Ankan: player has 4 identical tiles in hand
                # Remove 4 instances of the tile from hand
                kong_tile = discarded_tile  # The drawn tile
                for _ in range(4):
                    if kong_tile in self.player_hands[player_id]:
                        self.player_hands[player_id].remove(kong_tile)
                self.melds[player_id].append(['ankan', [kong_tile] * 4])
                
                # Draw a replacement tile from the dead wall
                new_tile = self.draw_tile_from_rinshan()
                
                socketio.emit('message', {'data': f"Player {players[player_id]['username']} DECLARED ANKAN (concealed kong) of {kong_tile}!"}, room=self.room_id)
                
                if new_tile:
                    player_sid = self._get_player_sid(player_id)
                    if player_sid:
                        socketio.emit('tile_drawn', {'tile': new_tile}, room=player_sid)
                
                self.broadcast_game_state()
                player_sid = self._get_player_sid(player_id)
                if player_sid: 
                    socketio.emit('your_turn_to_discard', {}, room=player_sid)
                return True
            
            elif action_type == 'kakan':
                # Kakan: add the drawn tile to an existing pong to make a kong
                kong_tile = discarded_tile  # The drawn tile
                # Find the existing pong in the player's melds
                for i, (meld_type, meld_tiles) in enumerate(self.melds[player_id]):
                    if meld_type == 'peng' and len(meld_tiles) == 3 and meld_tiles[0] == kong_tile:
                        # Remove the existing pong
                        self.melds[player_id].pop(i)
                        # Add the kong
                        self.melds[player_id].append(['kakan', [kong_tile] * 4])
                        break
                
                # Draw a replacement tile from the dead wall
                new_tile = self.draw_tile_from_rinshan()
                
                socketio.emit('message', {'data': f"Player {players[player_id]['username']} DECLARED KAKAN (added kong) of {kong_tile}!"}, room=self.room_id)
                
                if new_tile:
                    player_sid = self._get_player_sid(player_id)
                    if player_sid:
                        socketio.emit('tile_drawn', {'tile': new_tile}, room=player_sid)
                
                self.broadcast_game_state()
                player_sid = self._get_player_sid(player_id)
                if player_sid: 
                    socketio.emit('your_turn_to_discard', {}, room=player_sid)
                return True

        return False

    def next_turn(self):
        if not self.game_started: return
        current_index = self.player_ids.index(self.current_turn_player_id)
        next_player_id = self.player_ids[(current_index + 1) % len(self.player_ids)]
        self.current_turn_player_id = next_player_id
        self.last_discarded_tile = None
        
        drawn_tile = self.draw_tile(self.current_turn_player_id)
        # The tile_drawn event is now emitted from within the draw_tile method
        # if drawn_tile:
            # socketio.emit('message', {'data': f"Player {players[self.current_turn_player_id]['username']} drew a tile."}, room=self.room_id)
        
        self.broadcast_game_state()
        player_sid = self._get_player_sid(self.current_turn_player_id)
        if player_sid: socketio.emit('your_turn_to_discard', {}, room=player_sid)

    def draw_tile_from_rinshan(self):
        # This is a simplified Rinshan for now, assuming it's from the end of the wall.
        # In real Mahjong, Rinshan tiles are from a specific "dead wall" part of the main wall.
        if self.wall:
            return self.wall.pop(-1) # Pop from the end for Rinshan
        return None

    def broadcast_game_state(self):
        # Send partial game state to each player
        from server import players, socketio
        for p_id in self.player_ids:
            if p_id not in players: continue
            player_sid = players[p_id]['sid']
            if not player_sid: continue # Skip disconnected players

            player_state = {
                'room_id': self.room_id, 'game_started': self.game_started,
                'your_hand': self.player_hands.get(p_id, []),
                'your_melds': self.melds.get(p_id, []),
                'your_discard_pile': self.discard_piles.get(p_id, []),
                'current_turn_player': players.get(self.current_turn_player_id, {}).get('username', 'N/A'),
                'last_discarded_tile': self.last_discarded_tile, 'wall_count': len(self.wall),
                'is_dealer': (p_id == self.current_dealer_id), 'other_players': []
            }
            for other_p_id in self.player_ids:
                if other_p_id != p_id:
                    player_state['other_players'].append({
                        'username': players.get(other_p_id, {}).get('username', 'N/A'),
                        'discard_pile': self.discard_piles.get(other_p_id, []),
                        'melds': self.melds.get(other_p_id, []),
                        'hand_size': len(self.player_hands.get(other_p_id, []))
                    })
            socketio.emit('game_state_update', player_state, room=player_sid)

    def _get_player_sid(self, player_id):
        from server import players
        return players.get(player_id, {}).get('sid', None)

    # Tile conversion helper
    _tile_map = TILE_MAP
    _reverse_tile_map = REVERSE_TILE_MAP

    def _to_numeric_tiles(self, hand):
        numeric_hand = []
        for tile in hand:
            if tile in self._tile_map:
                numeric_hand.append(self._tile_map[tile])
            else:
                # Handle unknown tiles or expand _tile_map
                print(f"Warning: Unknown tile {tile} encountered.")
        return sorted(numeric_hand)

    def _count_tiles(self, numeric_hand):
        tile_counts = {}
        for tile in numeric_hand:
            tile_counts[tile] = tile_counts.get(tile, 0) + 1
        return tile_counts

    def _find_max_melds(self, tile_counts, num_melds=0, num_pairs=0):
        # Base case: if no tiles left, return current melds and pairs
        if not any(tile_counts.values()):
            return num_melds, num_pairs

        # Find the smallest tile available
        sorted_tiles = sorted([tile for tile, count in tile_counts.items() if count > 0])
        if not sorted_tiles:
            return num_melds, num_pairs
        
        current_tile_str = sorted_tiles[0]
        suit = current_tile_str[0]
        rank = int(current_tile_str[1:]) if len(current_tile_str) > 1 else 0 # 0 for Zihai (winds/dragons)

        max_m = num_melds
        max_p = num_pairs

        # Option 1: Try to form a set (triplet)
        if tile_counts.get(current_tile_str, 0) >= 3:
            new_counts = dict(tile_counts)
            new_counts[current_tile_str] -= 3
            m, p = self._find_max_melds(new_counts, num_melds + 1, num_pairs)
            if m > max_m or (m == max_m and p > max_p):
                max_m, max_p = m, p

        # Option 2: Try to form a sequence (straight) for numbered tiles
        if suit in ['m', 'p', 's'] and rank <= 7 and \
           tile_counts.get(current_tile_str, 0) >= 1 and \
           tile_counts.get(f"{suit}{rank+1}", 0) >= 1 and \
           tile_counts.get(f"{suit}{rank+2}", 0) >= 1:
            
            new_counts = dict(tile_counts)
            new_counts[current_tile_str] -= 1
            new_counts[f"{suit}{rank+1}"] -= 1
            new_counts[f"{suit}{rank+2}"] -= 1
            m, p = self._find_max_melds(new_counts, num_melds + 1, num_pairs)
            if m > max_m or (m == max_m and p > max_p):
                max_m, max_p = m, p
        
        # Option 3: If current_tile_str is not used in a meld, move to the next tile
        # This is crucial to avoid infinite recursion or missing combinations
        # Skip this tile and consider remaining tiles
        # Find the next tile to process
        next_tile_counts = dict(tile_counts)
        if next_tile_counts.get(current_tile_str, 0) > 0:
            next_tile_counts[current_tile_str] = 0 # Mark current tile as processed for this branch
            m, p = self._find_max_melds(next_tile_counts, num_melds, num_pairs) # Recurse without forming a meld with current_tile_str
            if m > max_m or (m == max_m and p > max_p):
                max_m, max_p = m, p

        return max_m, max_p

    def calculate_shanten(self, hand):
        numeric_hand = self._to_numeric_tiles(hand)
        tile_counts = self._count_tiles(numeric_hand)
        
        # Total number of tiles
        total_tiles = sum(tile_counts.values())

        if total_tiles == 0:
            return 0 # An empty hand is 0 shanten (or special case for starting game)
        
        # Ideal winning hand has N * 3 + 2 tiles (N melds + 1 pair)
        # We need to find the max number of melds and pairs we can form.
        
        # Max melds in a 13-tile hand is 4 (e.g., 4 sets + 1 pair, or 3 sets + 2 sequences + 1 pair)
        max_completed_melds = 0
        min_shanten = float('inf')

        # Try every tile as a potential pair
        potential_pairs = []
        for tile, count in tile_counts.items():
            if count >= 2:
                potential_pairs.append(tile)
        
        # If no pairs possible, consider adding a dummy pair later
        if not potential_pairs and total_tiles % 3 == 1: # Could be 13 tiles, aiming for 4 melds + 1 pair
            # If no pairs, we are at least 1 shanten away for the pair.
            # Shanten = (4 - melds) * 2 - (pairs > 0 ? 1 : 0) + (if 13 tiles, needs pair)
            # This logic needs careful refinement.
            pass # Handle no pairs case

        # Simplified Shanten calculation (needs refinement for complex mahjong rules like Chiitoitsu, Kokushi Musou)
        # For a standard hand (4 melds + 1 pair)

        # Base calculation:
        # Shanten = (number of incomplete groups + number of pairs needed) - 1
        # Number of groups = total_tiles // 3
        # Number of pairs = 1 (usually)

        # Iterate through possible hand compositions by removing a potential pair
        # For a 13-tile hand, we need 4 melds and 1 pair.
        # For a 14-tile hand (after drawing), we need 4 melds and 1 pair (and discard one).
        
        for i in range(2): # 0 for no pair, 1 for trying to form a pair
            temp_tile_counts = dict(tile_counts)
            current_pairs = 0
            # Try to form a pair with any tile if i == 1
            if i == 1:
                found_pair = False
                for tile in sorted(temp_tile_counts.keys()):
                    if temp_tile_counts.get(tile, 0) >= 2:
                        temp_tile_counts[tile] -= 2
                        current_pairs += 1
                        found_pair = True
                        break
                if not found_pair and total_tiles > 0: # If we need a pair but can't form one yet
                    continue # Skip this branch, it's not a valid path to tenpai with a pair right now

            melds_found = 0
            
            # Recursive function to count complete sets and sequences
            def count_complete_groups(counts):
                nonlocal melds_found
                max_melds = 0

                sorted_remaining_tiles = sorted([t for t, c in counts.items() if c > 0])
                if not sorted_remaining_tiles:
                    return 0

                tile = sorted_remaining_tiles[0]
                suit = tile[0]
                rank = int(tile[1:]) if len(tile) > 1 else 0

                # Try to form a triplet
                if counts.get(tile, 0) >= 3:
                    new_counts = dict(counts)
                    new_counts[tile] -= 3
                    max_melds = max(max_melds, 1 + count_complete_groups(new_counts))

                # Try to form a sequence (for numbered tiles)
                if suit in ['m', 'p', 's'] and rank <= 7 and \
                   counts.get(tile, 0) >= 1 and \
                   counts.get(f"{suit}{rank+1}", 0) >= 1 and \
                   counts.get(f"{suit}{rank+2}", 0) >= 1:
                    new_counts = dict(counts)
                    new_counts[tile] -= 1
                    new_counts[f"{suit}{rank+1}"] -= 1
                    new_counts[f"{suit}{rank+2}"] -= 1
                    max_melds = max(max_melds, 1 + count_complete_groups(new_counts))
                
                # Move to next tile if no meld is formed with current tile (crucial for exploration)
                # Ensure we don't process the same tile count multiple times in the same step
                if max_melds == 0: # If we didn't form a meld with the current tile
                    new_counts_skip = dict(counts)
                    new_counts_skip[tile] -= 1 # "Use" this tile as a non-meld component (e.g., part of a pair or a single)
                    max_melds = max(max_melds, count_complete_groups(new_counts_skip)) # Recurse on remaining

                return max_melds
            
            melds_found = count_complete_groups(temp_tile_counts)
            
            # Shanten calculation: (target_melds - actual_melds) + (target_pairs - actual_pairs)
            # For standard hand: 4 melds, 1 pair
            # For 13 tiles: target 4 melds, 1 pair. (14 tiles - 1 discard)
            
            # Simplified approach: calculate the number of "floating" tiles not part of completed groups
            remaining_tiles_after_melds = sum(temp_tile_counts.values()) # This needs careful handling after finding melds

            # The shanten calculation logic is still very simplified here.
            # A full shanten calculation considers "uketsuke" (accepting tiles) for incomplete groups.
            # This placeholder assumes a "closest to 4 melds + 1 pair" calculation.
            
            # This shanten calculation requires a more robust algorithm.
            # For now, I will use a very simplified heuristic.
            
            # Heuristic for shanten:
            # max_melds = number of sets/sequences that can be formed
            # max_pairs = number of pairs that can be formed
            # Shanten = 8 - (max_melds * 2) - (max_pairs) - (if hand is 14 tiles)
            
            # This is a highly simplified shanten for a first pass, as specified.
            # A proper shanten algorithm (e.g., based on the Japanese Mahjong Shanten algorithm)
            # would be much more involved, involving recursive search for valid groups and pairs
            # considering various hand types.
            
            # For 13 or 14 tile hand, goal is 4 sets + 1 pair.
            # Count the number of complete groups (sets and sequences) and isolated pairs.
            
            # Placeholder for proper shanten calculation
            # This part will require external libraries or a much more complex implementation.
            # For now, let's keep it simple to move forward.
            
            # To refine `calculate_shanten`:
            # 1. Count actual groups (sets and sequences)
            # 2. Count actual pairs
            # 3. Calculate blocks (incomplete sets/sequences)
            # 4. Use a formula like: (8 - 2*groups - pairs - blocks) where groups, pairs, blocks are maximized
            #    This is still a simplification.

            # Re-evaluating the shanten calculation, a common heuristic for standard hands is:
            # Shanten = 8 - 2 * (number of completed sets/sequences) - (number of pairs) - (number of "floating" tiles that form potential groups)

            # Let's try a simpler approach to count groups and pairs.
            max_groups = 0
            max_pairs = 0

            temp_counts_for_groups = dict(tile_counts)
            
            def find_groups_and_pairs(current_counts, current_groups, current_pairs):
                nonlocal max_groups, max_pairs
                max_groups = max(max_groups, current_groups)
                max_pairs = max(max_pairs, current_pairs)

                sorted_tiles = sorted([t for t, c in current_counts.items() if c > 0])
                if not sorted_tiles:
                    return

                tile = sorted_tiles[0]
                suit = tile[0]
                rank = int(tile[1:]) if len(tile) > 1 else 0

                # Try forming a triplet
                if current_counts.get(tile, 0) >= 3:
                    new_counts = dict(current_counts)
                    new_counts[tile] -= 3
                    find_groups_and_pairs(new_counts, current_groups + 1, current_pairs)
                
                # Try forming a sequence
                if suit in ['m', 'p', 's'] and rank <= 7 and \
                   current_counts.get(tile, 0) >= 1 and \
                   current_counts.get(f"{suit}{rank+1}", 0) >= 1 and \
                   current_counts.get(f"{suit}{rank+2}", 0) >= 1:
                    new_counts = dict(current_counts)
                    new_counts[tile] -= 1
                    new_counts[f"{suit}{rank+1}"] -= 1
                    new_counts[f"{suit}{rank+2}"] -= 1
                    find_groups_and_pairs(new_counts, current_groups + 1, current_pairs)
                
                # Try forming a pair
                if current_counts.get(tile, 0) >= 2:
                    new_counts = dict(current_counts)
                    new_counts[tile] -= 2
                    find_groups_and_pairs(new_counts, current_groups, current_pairs + 1)
                
                # Move to next tile if current tile cannot form any group or pair or has been processed
                new_counts = dict(current_counts)
                if new_counts.get(tile, 0) > 0:
                    new_counts[tile] = 0 # Mark as processed for this path
                    find_groups_and_pairs(new_counts, current_groups, current_pairs)

            find_groups_and_pairs(tile_counts, 0, 0)
            
            # Simple shanten for 4 melds + 1 pair hand
            # Need to consider if we have 13 tiles (need 1 tile to complete) or 14 tiles (need to discard)
            
            # This is still a highly complex topic. For the purpose of "simple backtracking algorithm"
            # as requested, I will provide a simplified shanten for now, mainly focusing on
            # counting completed groups and pairs.
            
            # A common approach for 13-tile hand (aiming for 4 melds, 1 pair)
            # Shanten = 8 - (melds * 2) - (pairs) - (blocks)
            # where blocks are incomplete sets or sequences
            
            # For simplicity, let's use a very basic approximation:
            # We need 4 melds and 1 pair.
            # Number of available tiles for groups is (total_tiles - 1 for pair)
            # Max melds we can form from the hand
            
            num_groups, num_pairs = self._find_max_melds(tile_counts) # Recursively finds max groups and pairs

            # Now, based on Japanese Mahjong Shanten calculation for 4 sets + 1 pair:
            # Shanten = 8 - (num_groups * 2) - (num_pairs)
            # This is a common heuristic but might not be perfectly accurate for all edge cases
            # or alternative hand structures (like Seven Pairs).

            # For a 13-tile hand, we are looking for 4 groups and 1 pair.
            # The current _find_max_melds only counts groups, not pairs explicitly.
            # Let's adjust _find_max_melds to return both max groups and max pairs it can form.
            # It already does.

            # Re-implementing shanten based on a more common strategy:
            # The goal for a standard hand is (Mentsu x 4) + (Jantou x 1).
            # Mentsu = a complete set (Triplet or Sequence)
            # Jantou = a pair
            
            # Let's count how many groups (sets/sequences) and pairs we can form.
            
            max_effective_groups = 0
            max_effective_pairs = 0

            def search_shanten(current_hand_counts, groups, pairs, isolated_tiles):
                nonlocal max_effective_groups, max_effective_pairs

                # Base cases
                if not any(current_hand_counts.values()):
                    max_effective_groups = max(max_effective_groups, groups)
                    max_effective_pairs = max(max_effective_pairs, pairs)
                    return

                sorted_remaining_tiles = sorted([t for t, c in current_hand_counts.items() if c > 0])
                if not sorted_remaining_tiles:
                    max_effective_groups = max(max_effective_groups, groups)
                    max_effective_pairs = max(max_effective_pairs, pairs)
                    return

                tile = sorted_remaining_tiles[0]
                suit = tile[0]
                rank = int(tile[1:]) if len(tile) > 1 else 0

                # Recursive branches:
                # 1. Form a triplet
                if current_hand_counts.get(tile, 0) >= 3:
                    new_counts = dict(current_hand_counts)
                    new_counts[tile] -= 3
                    search_shanten(new_counts, groups + 1, pairs, isolated_tiles)

                # 2. Form a sequence
                if suit in ['m', 'p', 's'] and rank <= 7 and \
                   current_hand_counts.get(tile, 0) >= 1 and \
                   current_hand_counts.get(f"{suit}{rank+1}", 0) >= 1 and \
                   current_hand_counts.get(f"{suit}{rank+2}", 0) >= 1:
                    new_counts = dict(current_hand_counts)
                    new_counts[tile] -= 1
                    new_counts[f"{suit}{rank+1}"] -= 1
                    new_counts[f"{suit}{rank+2}"] -= 1
                    search_shanten(new_counts, groups + 1, pairs, isolated_tiles)

                # 3. Form a pair
                if current_hand_counts.get(tile, 0) >= 2:
                    new_counts = dict(current_hand_counts)
                    new_counts[tile] -= 2
                    search_shanten(new_counts, groups, pairs + 1, isolated_tiles)

                # 4. If current tile is not used in a complete group or pair, move to the next tile
                new_counts = dict(current_hand_counts)
                new_counts[tile] -= 1
                search_shanten(new_counts, groups, pairs, isolated_tiles + 1)
            
            search_shanten(tile_counts, 0, 0, 0)

            # Shanten formula: (8 - 2 * groups - pairs) where groups is max possible, pairs is max possible from remaining
            # This is for 4 melds + 1 pair.
            shanten_val = max(0, 8 - (max_effective_groups * 2) - max_effective_pairs) # Max 4 groups, 1 pair -> 8 - 8 - 1 = -1 (tenpai or agari)
            
            # This formula is still a rough approximation for standard hands.
            # A truly accurate shanten calculation would be a significant undertaking.
            # For the purpose of "simple backtracking algorithm" as requested, this will be the approach.
            return shanten_val

    def is_ting(self, player_id):
        # A player is "ting" if their shanten is 0 after discarding one tile.
        # This function should be called after a player draws a tile, before they discard.
        # It needs to simulate discarding each tile and checking if the remaining hand is 0 shanten.
        
        # NOTE: This assumes a 14-tile hand where a discard is imminent.
        # If the hand is 13 tiles, it needs 1 tile to reach tenpai.
        
        hand = list(self.player_hands[player_id])
        
        if len(hand) % 3 != 2: # Hand should have 14 tiles to be "about to ting" by discarding one
            return False # Not in a state to declare ting by discarding

        # Iterate through each tile in hand, simulate discarding it, and check shanten
        for i in range(len(hand)):
            temp_hand = list(hand)
            discarded_candidate = temp_hand.pop(i)
            
            # Now, check shanten for the 13-tile hand.
            # If shanten is 0, it means the player is in tenpai after this discard.
            if self.calculate_shanten(temp_hand) == 0:
                return True
        return False