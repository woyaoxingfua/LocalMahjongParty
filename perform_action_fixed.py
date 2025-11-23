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