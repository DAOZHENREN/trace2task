from multiprocessing import freeze_support

from trace2task.desktop_app import main

if __name__ == "__main__":
    freeze_support()
    main()
