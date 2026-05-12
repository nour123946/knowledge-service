import json
import os


CLIENTS_DIR = "clients"


class ClientConfigError(Exception):
    pass


def load_client_config(client_id: str):

    path = os.path.join(CLIENTS_DIR, f"{client_id}.json")

    if not os.path.exists(path):
        raise ClientConfigError(f"Unknown client: {client_id}")

    with open(path, "r", encoding="utf-8") as f:
        config = json.load(f)

    validate_client_config(config)

    return config


def validate_client_config(config):

    if "features" not in config:
        raise ClientConfigError("Missing features configuration")

    features = config["features"]

    if features.get("directory") is True:

        if "supabase" not in config:
            raise ClientConfigError(
                "Directory feature requires supabase configuration"
            )

        supabase_config = config["supabase"]

        if "url_env" not in supabase_config:
            raise ClientConfigError("Missing Supabase URL env variable")

        if "anon_key_env" not in supabase_config:
            raise ClientConfigError("Missing Supabase key env variable")