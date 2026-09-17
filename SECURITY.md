# Security

Recursive Discovery is research software that can execute model-generated scientific code. Treat the execution boundary as security-sensitive.

## Execution modes

The preferred execution path is a container/VM worker with:

- network disabled unless explicitly required;
- dropped capabilities;
- no-new-privileges;
- explicit CPU, memory, PID, and time limits;
- declared inputs and outputs.

If no container runtime is available, the portable fallback is explicitly recorded as:

```text
process-not-hermetic
```

That fallback is **not** a security sandbox and should not be used for untrusted model-generated code in production.

## Filesystem and key separation

The current container adapter mounts its working directory. A container does not protect secrets
that are deliberately included in that mount. Keep untrusted job workspaces separate from the
ledger signing key, evaluator credentials, and private data. Process workers share the host's
permissions and are not suitable for hostile code.

Kernel signatures authenticate execution records under a shared secret; they do not validate the
scientific interpretation of a result or provide protection if that secret is accessible to the
executed program.

## Model-defined instruments

Recursive instruments use a constrained numerical expression interpreter. They do not receive imports, attribute access, file access, process creation, network access, or arbitrary function dispatch.

## Sealed evaluation

Prospective evaluation bytes should live outside the model-readable scientific store. The proposing model should not receive evaluator credentials or direct filesystem access to sealed data.

## Reporting vulnerabilities

Report security-sensitive issues through GitHub private vulnerability reporting. Please do not publish exploit details in a public issue. Non-sensitive reliability or correctness bugs can use the normal issue tracker.
