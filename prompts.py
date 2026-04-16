"""LLM prompt templates for ads generation."""

SYSTEM_PROMPT = (
    "You are an expert copywriter specializing in creating compelling advertisements. "
    "Always return your response as valid JSON with the following structure: "
    "{\"ads\": [\"ad1\", \"ad2\", ...]}"
)

USER_PROMPT = (
    "Generate {num_ads} ad copies for product '{product}' "
    "targeting audience '{audience}'. "
    "Return the results as JSON in the format: {{\"ads\": [...]}}"
)
