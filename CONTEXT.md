# Local MLX Inference Management

This context describes named local inference servers and the stable identities and endpoints used to manage them.

## Language

**Server Definition**:
A named declaration of one managed local inference server, including its server type, model alias, and client endpoint.
_Avoid_: Instance, process

**Server Type**:
The inference server family named by a Server Definition, currently `mlx_lm` or `optiq`.
_Avoid_: Backend, provider

**Model Alias**:
A stable local identifier that a Server Definition uses to select a model.
_Avoid_: Model name, model ID

**Model Reference**:
The repository identifier or filesystem path resolved by a Model Alias.
_Avoid_: Model Alias

**Client Endpoint**:
The stable loopback address where clients reach a managed server.
_Avoid_: Upstream Endpoint, server port

**Upstream Endpoint**:
A private loopback address allocated for the inference server behind a Client Endpoint.
_Avoid_: Client Endpoint, public endpoint

**Supervisor**:
The daemon that owns the lifecycle of managed local inference servers.
_Avoid_: Server, worker

**Probe**:
A liveness and model-introspection observation of a running inference server.
_Avoid_: Ping, status request
