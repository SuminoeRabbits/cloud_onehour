#
# pts_regression.py
#
# pts_regression.pyはtest_suite.jsonを入力としてPTS テストコマンドである
#
# ./script/run_pts_benchmark.py <testname> <number> 2>&1 \
# | tee -a ./reports/<machine>/<test categoly>/<testname>.log
#
# を生成します。なお./reports/<machine>/<test categoly>/は
# run_pts_benchmark.py が各テスト毎にログを出すディレクトリです。
# 
# 1. Test 生成について
# 2. <testname>の決定
# 3. <>
# 
