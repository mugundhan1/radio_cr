import numpy as np
import matplotlib.pyplot as plt
import pandas as pd
import glob

# folder where the dat files are located
FILE_EXT = ".DAT"
FOLDER = 'rfi_data/'

file_list = glob.glob(FOLDER+'*'+FILE_EXT)
print(file_list)

# open one file to read 
f = open(file_list[0],'rb')
data = np.fromfile(f,'<f4')
f.close

plt.plot(data)
plt.show()