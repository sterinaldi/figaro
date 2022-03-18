import traceback as tb

def except_hook(exctype, value, traceback):
    tb_last   = list(tb.walk_tb(traceback))[-1][0] # Get last call from traceback (function that raised the exception)
    tb_s2last = list(tb.walk_tb(traceback))[-2][0] # Get second-to-last call from traceback (hopefully FIGARO function, to be checked)
    # Check if error is due to some known improper usage of code
    #-----------#
    # Sample outside boundaries
    if exctype == ValueError and tb_s2last.f_code.co_name == "assign_to_cluster" and tb_last.f_code.co_name == "numpy.random.mtrand.RandomState.choice":
        print("You probably have a sample that falls outside the given boundaries. Please check.")
    else:
        sys.__excepthook__(exctype, value, traceback)
