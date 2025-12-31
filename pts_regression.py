#
# pts_regression.py
#
# pts_regression.pyはtest_suite.jsonを入力としてPTS テストコマンドである
#
# ./script/run_pts_benchmark.py <testname> <number> 2>&1 \
# | tee -a ./reports/<machine>/<test categoly>/<testname>.log
#
# を生成します。なおrun_pts_benchmark.py は
# run_pts_benchmark.py が各テスト毎にログを出すディレクトリです。
# 
# 1. Test 生成について
# test_suite.jsonで "enabled": true, のテストのみを有効とします。 
# "test_category"の層で"enabled": false, の場合、
# その配下のテストは全て無効となります。
# 2. <testname>の決定
# 有効なテストの"items"配下の"pts/<testname>"で開始するフィールドを
# <testname>とします。
# 3. <number>の決定
# <number>はtest_suite.jsonの有効なテストの"items"配下の
# "pts/<testname>"配下の
# "THFix_in_compile", "THChange_at_runtime", "TH_scaling"
# で決定されます。決定方法は以下の順番です。
#  if  "THFix_in_compile"==true
#       <number>=HardwareのCPU数、ie `nproc`
#  else
#    if "THChange_at_runtime"==true
#       <number> は何も指定してはいけない。
#    else
#       <number>=1
# 4. reports ディレクトリの決定
# reportsディレクトリは
# 5. 実行コマンドの発行
# test_suite.jsonで1-4までが終了しすべての実行コマンドが生成されたら
# 一度デバッグの為に標準出力に出力します。
# 
