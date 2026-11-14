extends Reference

# The legal action names a player's action panel shows, computed from the game
# state without the panel. This copies the non-turbo branch of
# ui/ActionSelector/ActionButtons.gd update_buttons() (and
# update_cancel_category_air_type), which is where the game keeps its
# legality rule: the core does not check legality (lab book
# docs/labbook/2026-11-13-core-game-scope-five-characters.md, decision 9).
#
# `extra` is the extra dict of the input under test. The panel recomputes its
# buttons from the extra-option toggles (input_grounded, input_aerial), so the
# legal set depends on the chosen extras as well as on the state.
#
# Call it on the live path, after game.gd's turn-start block has run, so that
# fighter.busy_interrupt and the states' on_interrupt() effects are set.
#
# Returns state names; a name ends in "*" when the action takes UI data.
# "Continue" (Hold) is always legal and is the first entry.
static func legal_actions(fighter, extra) -> Array:
	var state = fighter.state_machine.state
	var cancel_into
	if !fighter.busy_interrupt:
		cancel_into = (state.interrupt_into if !fighter.state_hit_cancellable else state.hit_cancel_into).duplicate(true)
		# ActionButtons.gd:610. fighter.feinting is the previous turn's feint.
		if fighter.feinting and fighter.should_free_cancel_allow_grounded_and_aerial_states() and fighter.opponent.current_state().busy_interrupt_type != CharacterState.BusyInterrupt.Hurt:
			cancel_into.append("Grounded")
			cancel_into.append("Aerial")
	else:
		cancel_into = state.busy_interrupt_into.duplicate(true)
	var force_grounded = extra != null and extra.has("input_grounded") and extra.input_grounded
	var force_aerial = extra != null and extra.has("input_aerial") and extra.input_aerial
	for i in range(cancel_into.size()):
		if force_grounded:
			cancel_into[i] = cancel_into[i].replace("Aerial", "Grounded")
		if force_aerial:
			cancel_into[i] = cancel_into[i].replace("Grounded", "Aerial")
	var names = ["Continue"]
	var out = ["Continue"]
	for category in cancel_into:
		if !fighter.action_cancels.has(category):
			continue
		for cancel_state in fighter.action_cancels[category]:
			if !cancel_state.show_in_menu or cancel_state.state_name in names:
				continue
			if !(cancel_state.is_usable_with_grounded_check(force_aerial, force_grounded) and cancel_state.allowed_in_stance()):
				continue
			if cancel_state.state_name == state.state_name:
				if fighter.state_hit_cancellable and !state.self_hit_cancellable:
					continue
				elif !fighter.state_hit_cancellable and !state.self_interruptable:
					continue
			if fighter.state_hit_cancellable and cancel_state.state_name in state.hit_cancel_exceptions:
				continue
			elif fighter.state_interruptable and cancel_state.state_name in state.interrupt_exceptions:
				continue
			var excepted = false
			if fighter.state_hit_cancellable:
				for c in state.hit_cancel_exceptions:
					if c in cancel_state.interrupt_from:
						excepted = true
			if !excepted and fighter.state_interruptable:
				for c in state.interrupt_exceptions:
					if c in cancel_state.interrupt_from:
						excepted = true
			if excepted:
				continue
			names.append(cancel_state.state_name)
			out.append(cancel_state.state_name + ("*" if cancel_state.data_ui_scene != null else ""))
	return out
