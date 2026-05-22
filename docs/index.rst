Sediment 문서
=============

Sediment는 HypeProof의 팀 메모리, 검색, 근거 기반 답변, 세션 보존을 위한
제품입니다. 이 문서는 현재 저장소의 설계 문서, 요구사항, 리서치, dogfood
기록을 배포 가능한 형태로 묶은 문서 사이트입니다.

언어 선택
---------

* `한국어 문서 <index.html>`_
* `English documentation <en/index.html>`_

핵심 스펙
---------

.. toctree::
   :maxdepth: 2

   specs/chat-session-requirements

리서치
------

.. toctree::
   :maxdepth: 1

   research/chat-session-benchmark-2026-05

설계
----

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
   design/15-conversation-retention
   design/15-self-improving-rag
   design/16-query-event-store
   design/ICP-segmentation
   design/cli-deployment
   design/cli-multi-user-access
   design/cli-test-requirements
   design/voice-ocr-connector-spec
   integration/from-openclaw

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

런북과 가이드
-------------

.. toctree::
   :maxdepth: 1

   MEMBER-GUIDE.ko
   sediment-cli-quickstart
   runbooks/supabase-pro-upgrade
   demo/boah-dental-flow

다이어그램
----------

* `Architecture diagram <architecture-diagram.html>`_
* `System flow <system-flow.html>`_

.. toctree::
   :hidden:

   en/index
