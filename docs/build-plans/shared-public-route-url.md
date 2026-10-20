# Shared public route URL Build Plan

PR #86's remaining helper scope repeats validated service-origin concatenation in MCP, health and beta auth. Its ordered-unique, lifecycle and doctor work has separate adopted owners. This build will consolidate only the URL string operation.

## Skills To Use

Software Build Plan will pin the scope before edits. Qodo rules will govern descriptive English names, ASCII authored strings, changed-function docstrings and compatibility across callers. Existing Python typing and settings-contract guidance will keep optional/required values and Pydantic validation unchanged. DX audit will compare the documented URL contract with the changed interfaces. Qodo Local Review will review the pushed base and final successor change before a ready PR; exact-head remote review and offline CI will remain merge gates.

## Contract and files

Base is pushed159 `bd3cafa`. A new `public_urls.py` will provide one pure function accepting an already validated service origin and route suffix. It'll preserve Pydantic's normalized origin and the current trailing-slash concatenation. MCP's two URL derivations, health's MCP URL and beta auth's three public properties will call it. Each caller will keep its current unset handling and error text. Host/origin checks, cookie rules, authentication, lifecycle, routes, Settings fields and provider calls won't change.

## Verification

Before extraction, public beta-auth properties and actual MCP HTTP responses will establish existing root paths, normalized host/default ports, explicit ports, IPv6 and unset behavior. Settings will continue rejecting credentials, non-root paths, queries and fragments. These compatibility witnesses will remain green after extraction; this build doesn't fix an observed URL behavior defect. Actual source inspection will establish the duplicate removal. Existing HTTP/auth/health suites, Ruff, Pyright, locked dependency checks and the full offline suite will precede final review. No hosted or live-provider call is needed.

## Delivery and limits

A separate successor PR will retain #86's historical branch and evidence. #86 won't close until its retained work is accounted for and the successor is natively merged. The build will reuse159's existing environment without installing dependencies. Production deployment and broader helper cleanup remain outside this change.
