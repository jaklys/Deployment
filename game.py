import matplotlib.pyplot as plt
from multiprocessing import Pool
from functools import partial
import os
import math


def from_string(name):
    directions = {
        'NW': (-1, 1),
        'N': (0, 1),
        'NE': (1, 1),
        'E': (1, 0),
        'SE': (1, -1),
        'S': (0, -1),
        'SW': (-1, -1),
        'W': (-1, 0),
    }
    return directions.get(name, None)

def next_direction(direction):
    direction_names = ['NW', 'N', 'NE', 'E', 'SE', 'S', 'SW', 'W']
    return direction_names[(direction_names.index(direction) + 1) % len(direction_names)]

def reverse_direction_name(direction):
    direction_names = ['NW', 'N', 'NE', 'E', 'SE', 'S', 'SW', 'W']
    return direction_names[(direction_names.index(direction) + 4) % len(direction_names)]

def clockwise_directions(start_direction):
    directions = []
    current = next_direction(start_direction)
    while current != start_direction:
        directions.append(current)
        current = next_direction(current)
    return directions

def is_on_same_line(point, direction, target):
    dx = target[0] - point[0]
    dy = target[1] - point[1]
    dir_vector = from_string(direction)

    if dir_vector[0] == 0:  # vertical direction 
        return dx == 0 and dy * dir_vector[1] > 0
    if dir_vector[1] == 0:  # horizontal direction
        return dy == 0 and dx * dir_vector[0] > 0

    return (dx * dir_vector[1] == dy * dir_vector[0]) and (dx * dir_vector[0] > 0) # diagonal directions

def distance(from_point, to_point):
    return math.sqrt((from_point[0] - to_point[0]) ** 2 + (from_point[1] - to_point[1]) ** 2)

def find_closest_player_in_direction(players_in_field, player, direction):
    potential_players = [other for other in players_in_field if is_on_same_line(player, direction, other)]
    if not potential_players:
        return None
    distance_from_player = partial(distance, player)
    return min(potential_players, key=distance_from_player)

def play_throw(players_in_field, player, direction):
    players_in_field.remove(player)

    for direction in clockwise_directions(direction):
        closest_player = find_closest_player_in_direction(players_in_field, player, direction)
        if closest_player:
            return closest_player, reverse_direction_name(direction)
    return None

def play_game(players, starting_direction, starting_player):
    players_in_field = set(players)
    current_direction = starting_direction
    current_player = players[starting_player - 1]
    number_of_throws = -1 # The starting player does not throw the ball
    path = []

    while True:
        round_result = play_throw(players_in_field, current_player, current_direction)
        number_of_throws += 1
        if not round_result:
            break
        next_player, current_direction = round_result
        path.append((current_player, next_player))
        current_player = next_player

    last_player_index = players.index(current_player)
    return number_of_throws, last_player_index + 1, path

def plot_game(players, path, starting_player, test_case_number):
    plt.figure(figsize=(10, 10))
    x_coords = [player[0] for player in players]
    y_coords = [player[1] for player in players]

    # Plot all players
    plt.scatter(x_coords, y_coords, color='blue', label='Players')
    # Highlight the starting player
    plt.scatter(players[starting_player - 1][0], players[starting_player - 1][1], color='red', label='Starting Player')

    # Add labels to the players
    for i, (x, y) in enumerate(players):
        plt.text(x, y, str(i + 1), fontsize=12, ha='right')

    # Draw the path of passes
    for throw in path:
        start, end = throw
        plt.arrow(start[0], start[1],
                  end[0] - start[0],
                  end[1] - start[1],
                  head_width=0.5, head_length=1, fc='green', ec='green')

    plt.xlabel('X coordinate')
    plt.ylabel('Y coordinate')
    plt.title(f'Test Case {test_case_number}: Player Positions and Ball Path')
    plt.legend()
    plt.grid(True)
    plt.savefig(f'test_case_{test_case_number}.png')  
    plt.close()  # 

def parse_input(file_path):
    with open(file_path, 'r') as file:
        lines = file.readlines()

    test_cases = []
    index = 0
    num_cases = int(lines[index].strip())
    index += 1

    for _ in range(num_cases):
        num_players = int(lines[index].strip())
        index += 1
        players = []
        
        for _ in range(num_players):
            x, y = map(int, lines[index].strip().split())
            players.append((x, y))
            index += 1

        starting_direction = lines[index].strip()
        index += 1
        starting_player = int(lines[index].strip())
        index += 1
        
        test_cases.append((players, starting_direction, starting_player))

    return test_cases

def process_game(test_case):
    players, starting_direction, starting_player = test_case
    result, last_player, path = play_game(players, starting_direction, starting_player)
    return (result, last_player, path)


if __name__ == "__main__":
    test_cases = parse_input('input.txt')
    
    with Pool(os.cpu_count()) as pool:
        results = pool.map(process_game, test_cases)

    for i, (throws, last_player, path) in enumerate(results):
        print(f"Test case {i + 1}: Throws = {throws}, Last Player = {last_player}")
        # plot_game(test_cases[i][0], path, test_cases[i][2], i + 1)

    with open('output.txt', 'w') as f:
        for i, (throws, last_player, _) in enumerate(results):
            f.write(f"{throws} {last_player}\n")