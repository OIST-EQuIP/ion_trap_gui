import numpy as np
import pyqtgraph as pg
import pyvisa
from rns_sma1000b import SMA1000B
import sys
from time import sleep

from pyqtgraph import PlotWidget
from PyQt5 import uic
from PyQt5.QtCore import QSettings, QTimer
from PyQt5.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QDoubleSpinBox,
    QLineEdit,
    QApplication,
)
from os import path

##########
## remember to conda develop or add the other dependencies as site packages or use .pth!!!
##########


class MainWindow(QMainWindow):
    RF_OPEN_TRAP_FILENAME = "/var/user/open_trap.lsw"
    RF_CLOSE_TRAP_FILENAME = "/var/user/close_trap.lsw"

    def __init__(self, *args, **kwargs):
        super(MainWindow, self).__init__(*args, **kwargs)

        # load main UI
        self.dir = path.dirname(path.abspath(__file__))
        uic.loadUi(path.join(self.dir, "mainwindow.ui"), self)

        # menu bar action
        self.save_action.triggered.connect(self.save_config)
        self.load_action.triggered.connect(self.load_config)
        self.delete_action.triggered.connect(self.delete_config)
        self.f5_rf_action.triggered.connect(self.init_rf_control)
        self.load_config()

        # load instruments
        self.rm = pyvisa.ResourceManager("@py")
        self.init_rf_control()

        # load graph
        self.init_graph()
        self.preview_volt_evol()
        self.lock_rf_control()

        # test
        self.rf.set_power(0.7)
        self.update_rf_status()

    def closeEvent(self, event):
        if not self.save_config():
            # save is cancelled
            print("not saved")
            event.ignore()
            return

        # close instruments
        instruments_list = ["rf"]
        for instrument in instruments_list:
            if hasattr(self, instrument):
                try:
                    getattr(self, instrument).close()
                except Exception as e:
                    print(e)

        return super().closeEvent(event)

    def save_config(self):
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Save Configuration")
        dlg.setText("Save and override previous configuration?")
        dlg.setStandardButtons(QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        choice = dlg.exec()
        if choice == QMessageBox.Yes:
            self.settings.setValue("last_rf_add", self.rf_address.currentText())
            settings_keys = [
                "rf_max_volt",
                "rf_open_trap_volt",
                "rf_close_trap_volt",
                "rf_step_int",
                "rf_num_steps",
            ]
            for key in settings_keys:
                val = getattr(self, key).value()
                self.settings.setValue(key, val)
            self.settings.setValue("rf_step_formula", self.rf_step_formula.text())

        # save geometry of window regardless
        self.settings.setValue("size", self.size())
        self.settings.setValue("pos", self.pos())
        self.settings.sync()

        return choice != QMessageBox.Cancel

    def delete_config(self):
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Delete Configuration")
        dlg.setText(
            "The saved configuration will be deleted. Restart the application for default values."
        )
        dlg.setStandardButtons(QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        choice = dlg.exec()
        if choice == QMessageBox.Yes:
            self.settings.clear()
            self.settings.sync()

    def load_config(self):
        self.settings = QSettings(
            path.join(self.dir, "configs.ini"), QSettings.IniFormat
        )
        print("Configurations loaded:", self.settings.allKeys())

        try:
            # geometry
            if self.settings.contains("size"):
                self.resize(self.settings.value("size"))
                self.move(self.settings.value("pos"))

            # rf
            if self.settings.contains("last_rf_add"):
                self.rf_address.addItem(self.settings.value("last_rf_add"))
                settings_keys = [
                    "rf_max_volt",
                    "rf_open_trap_volt",
                    "rf_close_trap_volt",
                    "rf_step_int",
                    "rf_num_steps",
                ]
                for key in settings_keys:
                    val = self.settings.value(key)
                    spinbox = getattr(self, key)
                    if type(spinbox) is QDoubleSpinBox:
                        getattr(self, key).setValue(float(val))
                    else:
                        getattr(self, key).setValue(int(val))
                self.rf_step_formula.setText(self.settings.value("rf_step_formula"))
        except Exception:
            print("Malformed configurations. Load partially.")

    def init_graph(self):
        self.plot_widget.setLabels(
            title="V(t) = V<sub>low</sub> + (V<sub>high</sub>-V<sub>low</sub>)Norm[f(t)]",
            left="V(t) [V]",
            bottom="t [s]",
        )
        self.plot_widget.setMouseEnabled(x=False, y=False)

    def init_rf_control(self):
        # timer
        self.rf_timer = QTimer()
        self.rf_timer.timeout.connect(self.simul_volt_evol)
        self.rf_remaining_time = 0

        # connection
        self.rf_address.clear()
        self.rf_address.addItem(SMA1000B.INSTRUMENT_NAME)
        self.rf_address.addItems(self.rm.list_resources())
        self.rf_connect_btn.clicked.connect(self.connect_rf)
        self.rf_max_volt.valueChanged.connect(
            lambda: self.lock_rf_control(
                "<font color='Green'>OK (Max voltage not set)</font>"
            )
        )
        self.rf_max_volt_btn.clicked.connect(self.set_rf_max_volt)
        self.rf_cur_freq.valueChanged.connect(
            lambda: self.rf_cur_freq_btn.setEnabled(True)
        )
        self.rf_cur_freq_btn.clicked.connect(self.set_rf_cur_freq)
        self.rf_cur_volt.valueChanged.connect(
            lambda: self.rf_cur_volt_btn.setEnabled(True)
        )
        self.rf_cur_volt_btn.clicked.connect(self.set_rf_cur_volt)
        self.rf_preview_btn.clicked.connect(self.preview_volt_evol)
        self.rf_open_trap_btn.clicked.connect(lambda: self.toggle_volt_evol(True))
        self.rf_close_trap_btn.clicked.connect(lambda: self.toggle_volt_evol(False))

        # voltage control
        self.rf_controls = [
            self.rf_cur_freq,
            self.rf_cur_volt,
            self.rf_open_trap_volt,
            self.rf_close_trap_volt,
            self.rf_step_int,
            self.rf_num_steps,
            self.rf_step_formula,
            self.rf_preview_btn,
        ]
        for control in self.rf_controls:
            if type(control) is QDoubleSpinBox:
                control.valueChanged.connect(
                    lambda: self.rf_open_trap_btn.setEnabled(False)
                )
                control.valueChanged.connect(
                    lambda: self.rf_close_trap_btn.setEnabled(False)
                )
            elif type(control) is QLineEdit:
                control.textChanged.connect(
                    lambda: self.rf_open_trap_btn.setEnabled(False)
                )
                control.textChanged.connect(
                    lambda: self.rf_close_trap_btn.setEnabled(False)
                )

        if self.connect_rf():
            self.update_rf_status()

    def connect_rf(self):
        try:
            self.lock_rf_control("Status: Connecting...")
            self.rf_status_label.repaint()
            instrument = self.rm.open_resource(self.rf_address.currentText())
            self.rf = SMA1000B(instrument)
            self.rf_status_label.setText(
                "Status: <font color='Green'>OK (Max voltage not set)</font>"
            )
            self.rf.set_state(1)
        except Exception as e:
            self.rf_status_label.setText(
                f"Status: <font color='red'>{str(e).split(':', 1)[1].strip()}</font>"
            )
        return self.is_rf_connected()

    def update_rf_status(self):
        self.rf_cur_freq.setValue(self.rf.get_frequency() / 1e6)
        self.rf_cur_volt.setValue(self.rf.get_power())
        print("pow", self.rf.pow)
        self.rf_cur_freq_btn.setEnabled(False)
        self.rf_cur_volt_btn.setEnabled(False)
        try:
            self.rf_sim_hline.setValue(self.rf.pow)
            print(self.rf.get_list_index(), self.rf_sim_vline.value(), "line")
            # self.rf_sim_vline.setValue(self.rf.get_list_index())
        except AttributeError:
            pass
        # TODO: actual status as well?

    def set_rf_max_volt(self):
        new_max_volts = self.rf_max_volt.value()
        self.rf.set_power_limit(new_max_volts)
        self.rf_cur_volt.setMaximum(new_max_volts)
        self.rf_open_trap_volt.setMaximum(new_max_volts)
        self.rf_close_trap_volt.setMaximum(new_max_volts)
        self.unlock_rf_control()

    def set_rf_cur_freq(self):
        self.rf.set_frequency(self.rf_cur_freq.value() * 1e6)
        self.rf_cur_freq_btn.setEnabled(False)

    def set_rf_cur_volt(self):
        self.rf.set_power(self.rf_cur_volt.value())
        self.rf_cur_volt_btn.setEnabled(False)
        self.rf_sim_hline.setValue(self.rf.pow)

    def preview_volt_evol(self):
        self.rf_remaining_time = 0
        V_open = float(self.rf_open_trap_volt.value())
        V_close = float(self.rf_close_trap_volt.value())
        T = float(self.rf_step_int.value())
        N = int(self.rf_num_steps.value())
        t = np.arange(0, T * (N + 1), T)

        try:
            if V_open > V_close:
                raise Exception(
                    "Trap opened voltage should be lower or equal to closed voltage"
                )

            formula = self.rf_step_formula.text() or "t"
            V_t = eval(formula)
            V_t = (V_t - V_t[0]) / (V_t[-1] - V_t[0]) * (V_close - V_open) + V_open
            print(t, V_t)
            # set power and dwell time list
            self.rf.set_list_sweep(
                pow_list=V_t,
                dwell_list=T,
                repeat=False,
                filename=self.RF_CLOSE_TRAP_FILENAME,
            )
            self.rf.set_list_sweep(
                pow_list=V_t[::-1],
                dwell_list=T,
                repeat=False,
                filename=self.RF_OPEN_TRAP_FILENAME,
            )

            self.plot_widget.clear()
            leg = self.plot_widget.addLegend(offset=1)
            self.plot_widget.plot(t, V_t[::-1], symbol="o", pen="g", name="opening")
            self.plot_widget.plot(t, V_t, symbol="o", pen="r", name="closing")
            leg.anchor((0.5, 0.5), (0.15, 0.5))
            self.rf_open_trap_btn.setEnabled(True)
            self.rf_close_trap_btn.setEnabled(True)
            # time tracking
            self.rf_sim_end = N
            self.rf_sim_vline = pg.InfiniteLine(0)
            self.rf_sim_hline = pg.InfiniteLine(self.rf.pow, angle=0)
            self.plot_widget.addItem(self.rf_sim_vline)
            self.plot_widget.addItem(self.rf_sim_hline)
            self.rf_timer.setInterval(int(T * 1000))
        except Exception as e:
            self.plot_widget.clear()
            text = pg.TextItem(repr(e), anchor=(0.5, 0.5))
            self.plot_widget.addItem(text)

        # to cancel all running timer and reset buttons
        self.end_volt_evol()

    def simul_volt_evol(self):
        self.rf_sim_vline.setValue(self.rf_sim * self.rf_timer.interval() / 1000)
        if self.rf_sim >= self.rf_sim_end:
            self.rf_open_trap_btn.setEnabled(False)
            self.rf_close_trap_btn.setEnabled(False)
            self.rf_sim_vline.setValue(0)
            self.end_volt_evol()
        self.rf_sim += 1

    def resume_simul_volt_evol(self, btn):
        self.simul_volt_evol()
        self.rf_timer.start()
        btn.setEnabled(True)

    def end_volt_evol(self, enable_btn=True):
        self.update_rf_status()
        self.rf_timer.stop()
        self.rf.stop_sweep()
        self.unlock_rf_control()
        if enable_btn:
            self.rf_open_trap_btn.setEnabled(True)
            self.rf_close_trap_btn.setEnabled(True)
        self.rf_open_trap_btn.setText("Open Trap")
        self.rf_close_trap_btn.setText("Close Trap")

    def toggle_volt_evol(self, open=True):
        if open:
            btn = self.rf_open_trap_btn
        else:
            btn = self.rf_close_trap_btn

        print(self.rf_remaining_time, self.rf_timer.isActive())
        if self.rf_remaining_time:
            print("resuming")
            # resuming
            # not locking rf control, so that it can be cancelled
            self.rf.start_list_sweep()
            QTimer.singleShot(
                self.rf_remaining_time + 1000, lambda: self.resume_simul_volt_evol(btn)
            )
            self.rf_remaining_time = 0
            btn.setText("Pause")
            btn.setEnabled(False)
        elif self.rf_timer.isActive():
            print("pausing")
            # pausing
            self.rf_remaining_time = self.rf_timer.remainingTime()
            self.end_volt_evol(False)
            btn.setText("Resume")
            QTimer.singleShot(1000, lambda: btn.setEnabled(True))
        else:
            print("starting")
            # starting
            if open:
                self.rf.change_list_sweep(self.RF_OPEN_TRAP_FILENAME)
            else:
                self.rf.change_list_sweep(self.RF_CLOSE_TRAP_FILENAME)
            self.rf.start_list_sweep()
            self.rf_timer.start()
            self.rf_sim = 1
            self.lock_rf_control()
            btn.setEnabled(True)
            btn.setText("Pause")

    def is_rf_connected(self):
        try:
            is_on = int(self.rf.instrument.query("SYST:STAR:COMP?"))
            self.rf_max_volt.setEnabled(True)
            self.rf_max_volt_btn.setEnabled(True)
        except Exception as e:
            if hasattr(self, "rf"):
                print(e)
            is_on = False
        if not is_on:
            self.rf_max_volt.setEnabled(False)
            self.rf_max_volt_btn.setEnabled(False)
            self.lock_rf_control()

        return is_on

    def lock_rf_control(self, msg=None):
        if msg:
            self.rf_status_label.setText(msg)
        for control in self.rf_controls:
            control.setEnabled(False)
        self.rf_open_trap_btn.setEnabled(False)
        self.rf_close_trap_btn.setEnabled(False)

    def unlock_rf_control(self):
        self.rf_status_label.setText("Status: <font color='green'>OK</font>")
        for control in self.rf_controls:
            control.setEnabled(True)


def main():
    app = QApplication(sys.argv)
    main = MainWindow()
    main.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    # running in development environment, so manually insert the system path
    # sys.path.insert(1, os.path.dirname(os.path.dirname(__file__)))

    main()
