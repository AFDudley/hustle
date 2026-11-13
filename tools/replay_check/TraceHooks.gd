extends "res://GameHooks.gd"

# Game hooks for the replay check. ReplayCheck.gd puts this script on the
# Hooks child of its Game node before the node enters the tree. post_tick is
# the end of game.tick(), after hitboxes and combo state resolve, so the
# snapshot sees one complete simulation tick.

var listener: FuncRef = null

func post_tick():
	if listener != null and !game.is_ghost:
		listener.call_func()
