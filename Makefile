# GNU make targets (spec 6.6 `make eval [AGENT]`). Machines without GNU make run the commands directly:
#   cd apps/api && uv run python -m app.aiops.evalrun [AGENT ...]
#   cd apps/api && uv run python -m app.aiops.promptgate

.PHONY: eval eval-live prompt-gate

# make eval                 every agent (mock model: a baseline)
# make eval AGENT=order_review
# make eval ARGS="--prompt patient_message_triage@2"
eval:
	cd apps/api && uv run python -m app.aiops.evalrun $(AGENT) $(ARGS)

eval-live:
	cd apps/api && uv run python -m app.aiops.evalrun --live $(AGENT) $(ARGS)

prompt-gate:
	cd apps/api && uv run python -m app.aiops.promptgate
