import uuid
import voluptuous as vol
from homeassistant import config_entries

from .const import CONF_INSTANCE_ID, CONF_JIBO_IP, CONF_SERVER_URL, DOMAIN


class JiboConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
            server_url = user_input[CONF_SERVER_URL].strip().rstrip("/")
            name = user_input.get("name", "").strip() or "OpenJibo"
            jibo_ip = user_input.get(CONF_JIBO_IP, "").strip()

            instance_id = str(uuid.uuid4())
            await self.async_set_unique_id(instance_id)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=name,
                data={
                    CONF_SERVER_URL: server_url,
                    CONF_INSTANCE_ID: instance_id,
                    CONF_JIBO_IP: jibo_ip,
                    "name": name,
                },
            )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_SERVER_URL): str,
                vol.Required("name"): str,
                vol.Optional(CONF_JIBO_IP, default=""): str,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )
