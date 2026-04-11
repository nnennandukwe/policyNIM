# PolicyNIM Architecture Diagram

This page gives a visual map of the current PolicyNIM architecture. For the
detailed design notes, package rules, and runtime constraints, see
[architecture.md](architecture.md).

## Module Boundary Map

```mermaid
flowchart LR
    classDef iface fill:#E8F1FF,stroke:#2457A6,color:#102A43,stroke-width:1.5px;
    classDef service fill:#FFF4E5,stroke:#C05621,color:#4A2B0F,stroke-width:1.5px;
    classDef adapter fill:#EEF8F3,stroke:#2F855A,color:#153E2D,stroke-width:1.5px;
    classDef shared fill:#F3F8EC,stroke:#5A7F2B,color:#24330E,stroke-width:1.3px;
    classDef local fill:#F5F7FA,stroke:#52606D,color:#1F2933,stroke-width:1.2px;
    classDef nvidia fill:#F5FAD8,stroke:#76B900,color:#243400,stroke-width:1.8px;
    classDef artifact fill:#F7F2FF,stroke:#6B46C1,color:#35215A,stroke-width:1.5px;

    subgraph Public["Public Interfaces"]
        direction TB
        CLI["CLI<br/>interfaces/cli.py"]
        MCP["MCP server<br/>interfaces/mcp.py"]
    end

    subgraph App["Application Services"]
        direction TB
        IngestSvc["IngestService"]
        SearchSvc["SearchService"]
        RouterSvc["PolicyRouterService"]
        PreflightSvc["PreflightService"]
        RuntimeDecisionSvc["RuntimeDecisionService"]
        RuntimeExecSvc["RuntimeExecutionService"]
        EvalSvc["EvalService"]
        DumpSvc["IndexDumpService"]
        HealthSvc["RuntimeHealthService"]
    end

    subgraph Adapters["Concrete Adapters"]
        direction TB
        IngestPkg["ingest/<br/>loader, parser, chunking"]
        NvidiaAdapter["providers/nvidia.py"]
        LanceStore["storage/lancedb.py"]
        RuntimeEvidenceStore["storage/runtime_evidence.py"]
    end

    subgraph Shared["Shared Core"]
        direction TB
        Settings["settings.py"]
        Types["types.py"]
        Contracts["contracts.py"]
    end

    subgraph Local["Local Inputs and Outputs"]
        direction TB
        Policies["policies/ corpus"]
        EvalSuite["evals/ suite"]
        RuntimeRules["runtime_rules.json"]
        RuntimeEvidenceDB["runtime_evidence.sqlite3"]
        Artifacts["data/ artifacts"]
        UI["Evidently UI"]
    end

    subgraph External["NVIDIA-hosted APIs"]
        direction TB
        Embed["Embeddings"]
        Rerank["Reranking"]
        Ground["Grounded generation"]
    end

    CLI --> IngestSvc
    CLI --> SearchSvc
    CLI --> RouterSvc
    CLI --> PreflightSvc
    CLI --> EvalSvc
    CLI --> DumpSvc
    MCP --> SearchSvc
    MCP --> PreflightSvc
    MCP --> HealthSvc

    Policies --> IngestPkg --> IngestSvc
    EvalSuite --> EvalSvc
    IngestSvc --> RuntimeRules
    RuntimeDecisionSvc --> RuntimeRules

    IngestSvc --> NvidiaAdapter
    SearchSvc --> NvidiaAdapter
    RouterSvc --> NvidiaAdapter
    PreflightSvc --> NvidiaAdapter
    RuntimeDecisionSvc --> LanceStore
    RuntimeExecSvc --> RuntimeDecisionSvc
    RuntimeExecSvc --> RuntimeEvidenceStore
    IngestSvc --> LanceStore
    SearchSvc --> LanceStore
    RouterSvc --> LanceStore
    PreflightSvc --> LanceStore
    PreflightSvc --> RouterSvc
    DumpSvc --> LanceStore
    HealthSvc --> LanceStore

    EvalSvc --> IngestSvc
    EvalSvc --> SearchSvc
    EvalSvc --> PreflightSvc
    EvalSvc --> Artifacts
    EvalSvc --> UI
    RuntimeExecSvc --> RuntimeEvidenceDB

    NvidiaAdapter --> Embed
    NvidiaAdapter --> Rerank
    NvidiaAdapter --> Ground

    IngestSvc -. typed requests and results .-> Types
    SearchSvc -. typed requests and results .-> Types
    RouterSvc -. typed requests and results .-> Types
    PreflightSvc -. typed requests and results .-> Types
    RuntimeDecisionSvc -. typed requests and results .-> Types
    RuntimeExecSvc -. typed requests and results .-> Types
    EvalSvc -. typed requests and results .-> Types
    HealthSvc -. typed requests and results .-> Types
    IngestSvc -. validated settings .-> Settings
    SearchSvc -. validated settings .-> Settings
    RouterSvc -. validated settings .-> Settings
    PreflightSvc -. validated settings .-> Settings
    RuntimeDecisionSvc -. validated settings .-> Settings
    RuntimeExecSvc -. validated settings .-> Settings
    EvalSvc -. validated settings .-> Settings
    HealthSvc -. validated settings .-> Settings
    IngestSvc -. contracts .-> Contracts
    SearchSvc -. contracts .-> Contracts
    RouterSvc -. contracts .-> Contracts
    PreflightSvc -. contracts .-> Contracts
    RuntimeDecisionSvc -. contracts .-> Contracts
    RuntimeExecSvc -. contracts .-> Contracts

    class CLI,MCP iface
    class IngestSvc,SearchSvc,RouterSvc,PreflightSvc,RuntimeDecisionSvc,RuntimeExecSvc,EvalSvc,DumpSvc,HealthSvc service
    class IngestPkg,NvidiaAdapter,LanceStore,RuntimeEvidenceStore adapter
    class Settings,Types,Contracts shared
    class Policies,EvalSuite,RuntimeRules,RuntimeEvidenceDB local
    class Embed,Rerank,Ground nvidia
    class Artifacts,UI artifact
```

## Runtime Flow

```mermaid
flowchart TB
    classDef input fill:#E8F1FF,stroke:#2457A6,color:#102A43,stroke-width:1.5px;
    classDef step fill:#FFF4E5,stroke:#C05621,color:#4A2B0F,stroke-width:1.5px;
    classDef store fill:#F5F7FA,stroke:#52606D,color:#1F2933,stroke-width:1.2px;
    classDef nvidia fill:#F5FAD8,stroke:#76B900,color:#243400,stroke-width:1.8px;
    classDef decision fill:#FFF6D9,stroke:#B7791F,color:#4A3600,stroke-width:1.5px;
    classDef result fill:#F7F2FF,stroke:#6B46C1,color:#35215A,stroke-width:1.5px;

    subgraph Ingest["Corpus to Index"]
        direction LR
        Policies["Policy Markdown corpus"] --> Parse["Parse and normalize"]
        Parse --> Chunk["Chunk by section and line span"]
        Chunk --> EmbedDocs["Embed documents"]
        EmbedDocs --> Index["Local LanceDB index"]
    end

    subgraph Search["Search Request"]
        direction LR
        Query["Search query"] --> EmbedQuery["Embed query"]
        EmbedQuery --> DenseSearch["Dense retrieve from index"]
        DenseSearch --> DomainFilter["Optional domain filter"]
        DomainFilter --> SearchRerank["Rerank top candidates"]
        SearchRerank --> SearchJSON["SearchResult JSON"]
    end

    subgraph Preflight["Grounded Preflight"]
        direction LR
        Task["Coding task"] --> PreflightRoute["Route selected evidence"]
        PreflightRoute --> Generate["Generate grounded draft"]
        Generate --> Validate["Validate cited chunk IDs"]
        Validate --> Enough{"Grounded enough?"}
        Enough -- yes --> PreflightJSON["PreflightResult JSON"]
        Enough -- no --> Insufficient["insufficient_context=true"]
    end

    subgraph Route["Policy Route Request"]
        direction LR
        RouteTask["Coding task"] --> ProfileTask["Infer task profile"]
        ProfileTask --> EmbedRouteTask["Embed task"]
        EmbedRouteTask --> DenseRoute["Dense retrieve from index"]
        DenseRoute --> RouteRerank["Rerank with profile signals"]
        RouteRerank --> SelectPolicies["Select policy packet"]
        SelectPolicies --> RouteJSON["PolicySelectionPacket JSON"]
    end

    subgraph Runtime["Runtime Decisions"]
        direction LR
        Action["Runtime action request"] --> LoadRules["Load runtime rules artifact"]
        LoadRules --> MatchRules["Match local policy rules"]
        MatchRules --> Decision["Allow / confirm / block"]
        Decision --> Execute["Optionally execute sanitized action"]
        Execute --> Persist["Append SQLite evidence"]
    end

    subgraph Hosted["Hosted HTTP Readiness"]
        direction LR
        HealthRoute["GET /healthz"] --> HealthRuntime["RuntimeHealthService"]
        HealthRuntime --> HealthJSON["HealthCheckResult JSON"]
    end

    subgraph Eval["Evaluation"]
        direction LR
        EvalSuite["Bundled eval suite"] --> EvalRun["Run offline or live evals"]
        EvalRun --> Compare["Compare rerank on and off"]
        Compare --> Reports["JSON artifacts and HTML reports"]
        Reports --> UI["Optional Evidently UI"]
    end

    Index -. vector lookup .-> DenseSearch
    Index -. vector lookup .-> DenseRoute
    PreflightRoute -. task-aware selection .-> SelectPolicies
    RuntimeRules -. compiled rules .-> LoadRules
    Index -. indexed evidence .-> MatchRules
    RuntimeEvidenceDB -. persisted evidence .-> Persist

    EmbedDocs --> Embeddings["NVIDIA embeddings"]
    EmbedQuery --> Embeddings
    EmbedRouteTask --> Embeddings
    SearchRerank --> RerankAPI["NVIDIA reranking"]
    RouteRerank --> RerankAPI
    Generate --> GroundAPI["NVIDIA grounded generation"]

    class Policies,Query,Task,RouteTask,EvalSuite input
    class Parse,Chunk,EmbedDocs,EmbedQuery,DenseSearch,DomainFilter,SearchRerank,ProfileTask,EmbedRouteTask,DenseRoute,RouteRerank,SelectPolicies,PreflightRoute,Generate,Validate,EvalRun,Compare,Reports step
    class Index store
    class Action input
    class LoadRules,MatchRules,Decision,Execute,Persist step
    class Embeddings,RerankAPI,GroundAPI nvidia
    class Enough decision
    class HealthRoute input
    class HealthRuntime step
    class HealthJSON result
    class SearchJSON,RouteJSON,PreflightJSON,Insufficient,UI result
```

## Reading Notes

- Blue nodes are public entry points or user-supplied inputs.
- Orange nodes are local application steps owned by PolicyNIM.
- Green-yellow nodes are NVIDIA-hosted model calls.
- Purple nodes are returned outputs or local viewing surfaces.
