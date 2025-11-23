import eventlet
import random
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import eventlet.event # Import eventlet.event for Event class
from mahjong_game import MahjongGame
from utils import get_local_ip

eventlet.monkey_patch()

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, async_mode='eventlet')

# --- Data Structures ---
# games: {room_id: MahjongGame_instance}
games = {}
# players: {player_id: {'username': str, 'sid': str, 'room': str}}
players = {}
# sid_to_player_id: {sid: player_id} - for quick lookups on disconnect
sid_to_player_id = {}
player_id_counter = 0


@app.route('/')
def index():
    return render_template('index.html')


@socketio.on('connect')
def handle_connect():
    global player_id_counter
    sid = request.sid
    reconnect_player_id = request.args.get('player_id')
    
    player_id = None
    # Try to reconnect the player
    if reconnect_player_id:
        try:
            player_id = int(reconnect_player_id)
            if player_id in players:
                # Player found, update their SID
                players[player_id]['sid'] = sid
                sid_to_player_id[sid] = player_id
                username = players[player_id]['username']
                room_id = players[player_id]['room']
                
                if room_id and room_id in games:
                    join_room(room_id)
                    print(f"Player {username} (ID: {player_id}) reconnected with new SID: {sid}")
                    emit('message', {'data': f"Welcome back, {username}!"}, room=sid)
                    # Resend the full game state to the reconnected player
                    games[room_id].broadcast_game_state()
                else: # Game may have ended while they were away
                    emit('message', {'data': 'The game you were in has ended. Please refresh to start a new one.'}, room=sid)
                return # End handling for reconnected player
            else:
                # Invalid player_id provided, treat as a new player
                player_id = None
        except (ValueError, TypeError):
            player_id = None # Invalid player_id format

    # New player connection
    if player_id is None:
        player_id_counter += 1
        player_id = player_id_counter
        username = request.args.get('username', f'Player{player_id}')
        players[player_id] = {'username': username, 'sid': sid, 'room': None}
        sid_to_player_id[sid] = player_id
        
        print(f"New client {username} (ID: {player_id}) connected with SID: {sid}")
        # Send the new player their ID to store for reconnections
        emit('player_info', {'player_id': player_id, 'username': username}, room=sid)

    # Assign player to a room
    found_room = False
    for r_id, game in games.items():
        if len(game.player_ids) < 4 and not game.game_started:
            if player_id not in game.player_ids: # Ensure they aren't already in
                game.add_player(player_id)
                players[player_id]['room'] = r_id
                join_room(r_id)
                socketio.emit('message', {'data': f"{username} joined room {r_id}"}, room=r_id)
                found_room = True
                if len(game.player_ids) == 4:
                    game.start_game()
                break
    
    if not found_room:
        new_room_id = f"room_{random.randint(1000, 9999)}"
        # Get special hands configuration from request or use default
        special_hands_config = request.args.get('special_hands_config')
        if special_hands_config:
            import json
            try:
                special_hands_config = json.loads(special_hands_config)
            except:
                special_hands_config = None
        game = MahjongGame(new_room_id, special_hands_config)
        games[new_room_id] = game
        game.add_player(player_id)
        players[player_id]['room'] = new_room_id
        join_room(new_room_id)
        socketio.emit('message', {'data': f"{username} created and joined new room {new_room_id}"}, room=new_room_id)


@socketio.on('create_room_with_config')
def on_create_room_with_config(data):
    sid = request.sid
    if sid not in sid_to_player_id:
        return
    
    player_id = sid_to_player_id[sid]
    username = players[player_id]['username']
    room_id = players[player_id]['room']
    
    # If player is already in a room, remove them from it
    if room_id in games:
        game = games[room_id]
        if player_id in game.player_ids:
            game.player_ids.remove(player_id)
        leave_room(room_id)
    
    # Create new room with special hands configuration
    new_room_id = f"room_{random.randint(1000, 9999)}"
    special_hands_config = data.get('special_hands_config', None)
    
    game = MahjongGame(new_room_id, special_hands_config)
    games[new_room_id] = game
    game.add_player(player_id)
    players[player_id]['room'] = new_room_id
    join_room(new_room_id)
    
    emit('room_created', {
        'room_id': new_room_id,
        'special_hands_config': game.special_hands_config
    }, room=sid)
    socketio.emit('message', {'data': f"{username} created new room {new_room_id} with custom special hands configuration"}, room=new_room_id)


@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    if sid not in sid_to_player_id: return
    
    player_id = sid_to_player_id[sid]
    if player_id not in players: return

    username = players[player_id]['username']
    room_id = players[player_id]['room']
    
    print(f"Player {username} (ID: {player_id}) disconnected.")
    # Don't delete the player from `players` dict, just mark their sid as None
    players[player_id]['sid'] = None
    del sid_to_player_id[sid]
    
    if room_id in games:
        game = games[room_id]
        if game.game_started:
            # If a game is in progress, just notify others. Don't end the game immediately.
            socketio.emit('message', {'data': f"{username} has disconnected. Waiting for reconnection..."}, room=room_id)
        else:
            # If game not started, treat as leaving for good
            if player_id in game.player_ids: game.player_ids.remove(player_id)
            del players[player_id] # Can delete fully if game hasn't started
            socketio.emit('message', {'data': f"{username} left the room."}, room=room_id)
            if not game.player_ids: # If room is empty, delete it
                del games[room_id]


@socketio.on('discard_tile')
def on_discard_tile(data):
    sid = request.sid
    if sid not in sid_to_player_id: return
    player_id = sid_to_player_id[sid]
    room_id = players[player_id]['room']
    if room_id in games:
        game = games[room_id]
        if not game.discard_tile(player_id, data.get('tile')):
            emit('error', {'message': 'Failed to discard tile.'}, room=sid)


@socketio.on('player_action')
def on_player_action(data):
    sid = request.sid
    if sid not in sid_to_player_id: return
    player_id = sid_to_player_id[sid]
    room_id = players[player_id]['room']
    if room_id in games:
        game = games[room_id]
        if not game.perform_action(player_id, data.get('action_type')):
            emit('error', {'message': f'Failed to perform {data.get("action_type")} action.'}, room=sid)


if __name__ == '__main__':
    local_ip = get_local_ip()
    port = 5000
    print(f"Server running at: http://{local_ip}:{port}")
    socketio.run(app, host='0.0.0.0', port=port)