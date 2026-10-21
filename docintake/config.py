"""Settings shared by the extractor and the validator."""

# The company that owns this paperwork. It is never the counterparty on a document,
# so the prompt tells the model so and the validator flags it if the model slips.
COMPANY = "Northern Pine Lumber Co."
