# Changelog

## 4.7.0 — first release of this fork

This release builds on upstream 4.6. Install this version on both Home Assistant
instances when using remote registry and loop protection features.

### Configuration

- Choose remote entities and domains during initial setup, with include and
  exclude controls that can search large remote installations. New connections
  no longer import everything by accident.
- Replace the linear options flow with a menu for entity selection, naming,
  proxy services and subscribed events. Each section saves independently.
- Add reconfiguration for host, port, access token and TLS settings, plus a
  reauthentication flow for expired or revoked tokens.
- Manage attribute filters as individual configuration subentries. Existing
  UI filters migrate automatically; YAML filters stay in YAML. Filter limits
  set to zero now work correctly.
- Add Italian translations and update German, Portuguese (Brazil) and Slovak
  configuration text for the new menu.

### Remote behavior and reliability

- Keep imported entity IDs and names consistent with configured prefixes;
  remove stale registry entries after a successful synchronization.
- Prevent states imported from another instance from bouncing between remote
  connections; mirror source devices and areas for imported entities.
- Forward service targets for remote entities, including entity, device, area,
  floor and label targets.
- Improve startup, reconnection, WebSocket response cleanup and shutdown.
  Avoid blocking Home Assistant startup when the remote is unavailable.
- Provide downloadable connection diagnostics without exposing access tokens
  and replace deprecated device registry iteration.

### Before updating

- Requires Home Assistant 2025.3 or newer for configuration subentries.
- Existing UI attribute filters move from options into subentries on first
  setup after the upgrade. Make a Home Assistant backup before updating.
- Entity IDs with a configured prefix may change to reflect that prefix;
  check automations and dashboards that refer to them.
- Install this fork on both instances to enable loop protection in both
  directions. On the remote instance keep `remote_homeassistant: instances:`
  in `configuration.yaml` when using the remote endpoint.
