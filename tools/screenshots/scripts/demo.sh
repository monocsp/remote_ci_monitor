#!/bin/sh
# 추적 시연용 잡 — 스텝 마커를 찍으며 40초쯤 돈다. RCM_INPUT_SPEED=fast 면 10초.
d=3; [ "${RCM_INPUT_SPEED:-normal}" = fast ] && d=1
echo "::rcm::steps::4"
echo "demo job #$RCM_JOB_ID for $RCM_REQUESTER (speed ${RCM_INPUT_SPEED:-normal})"
echo "::rcm::step::fetch deps";   sleep $d; echo "deps ok";      echo "::rcm::step-end::ok"
echo "::rcm::step::build";        sleep $((d*4)); echo "build ok"; echo "::rcm::step-end::ok"
echo "::rcm::step::unit tests";   sleep $((d*5)); echo "42 passed"; echo "::rcm::step-end::ok"
echo "::rcm::step::package";      sleep $((d*2)); echo "artifact demo.tar.gz"; echo "::rcm::step-end::ok"
echo "::rcm::summary::demo ok (${RCM_INPUT_SPEED:-normal})"
