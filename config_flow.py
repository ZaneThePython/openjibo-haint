import voluptuous as vol
from homeassistant import config_entries

from .const import DOMAIN

class JiboConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="Jibo", data=user_input)

        data_schema = vol.Schema({
            vol.Required("jibo_ip"): str
        })
        return self.async_show_form(step_id="user", data_schema=data_schema)