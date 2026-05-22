Sediment Documentation
======================

This site publishes the existing Sediment documentation from the repository.
The canonical chat/session requirements are written in reStructuredText; most
existing design notes remain Markdown and are rendered through MyST.

Canonical Specs
---------------

.. toctree::
   :maxdepth: 2

   specs/chat-session-requirements

Research
--------

.. toctree::
   :maxdepth: 1

   research/chat-session-benchmark-2026-05

Design
------

.. toctree::
   :maxdepth: 1

   design/README
   design/01-architecture-overview
   design/02-multitenancy-and-rbac
   design/03-auth
   design/04-collection-engine
   design/05-distillation-pipeline
   design/06-retrieval-and-chat
   design/07-notifications
   design/08-cost-and-observability
   design/09-validator-harness
   design/10-frontend
   design/11-deployment
   design/12-source-kinds-catalog
   design/13-tenant-catalog
   design/14-reliability-and-grounding
   design/ICP-segmentation
   design/cli-deployment
   design/cli-multi-user-access
   design/cli-test-requirements
   design/voice-ocr-connector-spec

Dogfood
-------

.. toctree::
   :maxdepth: 1

   dogfood/DOGFOOD_OVERNIGHT_SUMMARY
   dogfood/DOGFOOD_OVERNIGHT_2_SUMMARY
   dogfood/LOOP_RUNBOOK
   dogfood/discord-ingest-mother-contract
   dogfood/internal-loop
   dogfood/owned-task-1on1
   dogfood/sediment-dogfood-channel
   dogfood/trigger-bot-spec

Runbooks and Guides
-------------------

.. toctree::
   :maxdepth: 1

   MEMBER-GUIDE.ko
   sediment-cli-quickstart
   runbooks/supabase-pro-upgrade
   demo/boah-dental-flow

Diagrams
--------

* `Architecture diagram <architecture-diagram.html>`_
* `System flow <system-flow.html>`_
