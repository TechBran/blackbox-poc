class RetroCalculator {
    constructor() {
        this.display = document.getElementById('display');
        this.history = document.getElementById('history');
        this.currentValue = '0';
        this.previousValue = null;
        this.operator = null;
        this.waitingForOperand = false;

        this.initEventListeners();
    }

    initEventListeners() {
        const buttons = document.querySelectorAll('.btn');
        buttons.forEach(button => {
            button.addEventListener('click', () => this.handleButtonClick(button));
        });

        // Keyboard support
        document.addEventListener('keydown', (e) => this.handleKeyPress(e));
    }

    handleButtonClick(button) {
        if (button.dataset.number !== undefined) {
            this.inputDigit(button.dataset.number);
        } else if (button.dataset.operator !== undefined) {
            this.inputOperator(button.dataset.operator);
        } else if (button.dataset.action !== undefined) {
            this.performAction(button.dataset.action);
        }

        // Add haptic feedback
        if (navigator.vibrate) {
            navigator.vibrate(10);
        }
    }

    handleKeyPress(e) {
        if (e.key >= '0' && e.key <= '9') {
            this.inputDigit(e.key);
        } else if (e.key === '.') {
            this.performAction('decimal');
        } else if (e.key === '=' || e.key === 'Enter') {
            this.performAction('equals');
        } else if (e.key === 'Escape' || e.key === 'c' || e.key === 'C') {
            this.performAction('clear');
        } else if (e.key === '+' || e.key === '-' || e.key === '*' || e.key === '/') {
            this.inputOperator(e.key);
        } else if (e.key === '%') {
            this.performAction('percent');
        } else if (e.key === 'Backspace') {
            this.backspace();
        }
    }

    inputDigit(digit) {
        if (this.waitingForOperand) {
            this.currentValue = String(digit);
            this.waitingForOperand = false;
        } else {
            this.currentValue = this.currentValue === '0'
                ? String(digit)
                : this.currentValue + digit;
        }
        this.updateDisplay();
    }

    inputOperator(nextOperator) {
        const inputValue = parseFloat(this.currentValue);

        if (this.previousValue === null) {
            this.previousValue = inputValue;
        } else if (this.operator) {
            const result = this.calculate(this.previousValue, inputValue, this.operator);
            this.currentValue = String(result);
            this.previousValue = result;
        }

        this.waitingForOperand = true;
        this.operator = nextOperator;
        this.updateHistory();
        this.updateDisplay();
    }

    calculate(left, right, operator) {
        switch (operator) {
            case '+':
                return left + right;
            case '-':
                return left - right;
            case '*':
                return left * right;
            case '/':
                return right !== 0 ? left / right : 0;
            default:
                return right;
        }
    }

    performAction(action) {
        switch (action) {
            case 'clear':
                this.clear();
                break;
            case 'sign':
                this.toggleSign();
                break;
            case 'percent':
                this.percent();
                break;
            case 'decimal':
                this.inputDecimal();
                break;
            case 'equals':
                this.equals();
                break;
        }
    }

    clear() {
        this.currentValue = '0';
        this.previousValue = null;
        this.operator = null;
        this.waitingForOperand = false;
        this.updateDisplay();
        this.updateHistory();
    }

    toggleSign() {
        this.currentValue = String(parseFloat(this.currentValue) * -1);
        this.updateDisplay();
    }

    percent() {
        this.currentValue = String(parseFloat(this.currentValue) / 100);
        this.updateDisplay();
    }

    inputDecimal() {
        if (this.waitingForOperand) {
            this.currentValue = '0.';
            this.waitingForOperand = false;
        } else if (this.currentValue.indexOf('.') === -1) {
            this.currentValue += '.';
        }
        this.updateDisplay();
    }

    equals() {
        const inputValue = parseFloat(this.currentValue);

        if (this.previousValue !== null && this.operator) {
            const result = this.calculate(this.previousValue, inputValue, this.operator);
            this.currentValue = String(result);
            this.previousValue = null;
            this.operator = null;
            this.waitingForOperand = true;
            this.updateDisplay();
            this.updateHistory();
        }
    }

    backspace() {
        if (!this.waitingForOperand) {
            this.currentValue = this.currentValue.slice(0, -1) || '0';
            this.updateDisplay();
        }
    }

    updateDisplay() {
        // Format large numbers with scientific notation
        let displayValue = this.currentValue;
        if (displayValue.length > 12) {
            const num = parseFloat(displayValue);
            displayValue = num.toExponential(6);
        }
        this.display.textContent = displayValue;
    }

    updateHistory() {
        if (this.previousValue !== null && this.operator) {
            const operatorSymbols = {
                '+': '+',
                '-': '−',
                '*': '×',
                '/': '÷'
            };
            this.history.textContent = `${this.previousValue} ${operatorSymbols[this.operator] || this.operator}`;
        } else {
            this.history.textContent = '0';
        }
    }
}

// Initialize calculator when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    new RetroCalculator();
});
