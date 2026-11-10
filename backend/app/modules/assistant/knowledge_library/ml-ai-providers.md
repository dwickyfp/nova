# ML Models, AI Providers, and Neva

Keywords: machine learning, ML Models, model training, classification, regression, forecast, anomaly, clustering, AI Providers, LLM, embedding model, Neva, agent, Agent Studio, Nova Studio, Skills, Tools.

ML Models stores persistent model versions and their training lineage. Nova's native ML worker runs classification, regression, forecasting, anomaly detection, and clustering over caller-authorized StarRocks data. The SQL Workspace supports Nova `CREATE ML_MODEL`; AI & ML → ML Models shows the model lifecycle. `ml_execute` is Neva's bounded execution tool when available. Neva plans and explains an analysis; the worker performs numerical training and prediction. Feature Groups can provide pinned feature versions to a model training run. This is separate from the Feature Store, which defines reusable inputs rather than the trained estimator.

AI Providers (Administrator → AI Providers) configures model connections. Model types distinguish LLMs, which generate assistant and AI function responses, from embedding models, which turn text into vectors for semantic or hybrid AI Search. An embedding entry records alias, revision, dimensions, and similarity metric. The provider credential must remain in the existing secret path and must never be placed in an Intelligence object or Neva answer. AI SQL functions use configured LLM providers; they are not native ML training.

Neva is the built-in assistant in Nova. Agent Studio (AI & ML → Agent) configures specialist agents, instructions, tools, skills, and published Semantic Views; Nova Studio is the separate full-page work surface. Both use the bounded assistant engine. Neva can perform supported Nova UI operations through its action tools, including creating and publishing a Semantic View from authorized tables. The selected tools and the caller's StarRocks privileges determine what actions can run. A feature explanation needs reference knowledge, while a data read or operation needs the corresponding registered tool and its consent policy. An agent configuration never grants database access.

The Skills page (AI & ML → Skills) manages reusable agent procedures; the Tools page (AI & ML → Tools) manages capabilities an agent may select. A skill supplies guidance, while a tool performs an action under the assistant's guard, consent, and caller-access rules.

