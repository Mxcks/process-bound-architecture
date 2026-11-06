# packages/ingestion - Ingestion Layer

Separate layer 1 of the operational pipeline. Ingestion does exactly one
job: receive a byte stream, classify its origin, protocol, and routing
intent at the ingress boundary, and hand it to the runtime layer with an
ingress vector. It does not store policy, does not sync state, and does
not validate payload behavior.

Inputs: raw byte stream + ingress metadata (origin, protocol, intent).
Outputs: routed stream + IngressVector (one of the four EAI inputs).
Failure mode: an unclassifiable stream is refused at ingress, never
passed downstream unclassified.
