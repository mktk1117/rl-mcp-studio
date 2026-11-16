"""HTTP and websocket routes, one module per area of the API.

Each module has `mount(app, studio)`, which registers its handlers on the app
with the shared state in `rlmcp_studio.context.Studio`. `app.py` mounts them
in order.
"""
