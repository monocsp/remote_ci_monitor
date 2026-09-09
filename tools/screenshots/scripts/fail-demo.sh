#!/bin/sh
# 실패 시연용 — 두 번째 스텝에서 실패해 선언된 failed_step · summary · 이름별 이력이
# 어떻게 보이는지.
echo "::rcm::steps::3"
echo "::rcm::step::lint";  sleep 2; echo "lint ok";  echo "::rcm::step-end::ok"
echo "::rcm::step::tests"; sleep 3; echo "FAILED tests/test_x.py::test_y - AssertionError"
echo "::rcm::fail::tests/test_x.py::test_y"   # 무엇이 깨졌는지 이름으로 — 최근 이력이 붙는다
echo "::rcm::step-end::fail"
echo "::rcm::summary::1 test failed"
exit 1
