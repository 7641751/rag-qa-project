import '@testing-library/jest-dom';

// jsdom 未实现 scrollIntoView，测试里桩掉，避免 ChatWindow 自动滚动报错
Element.prototype.scrollIntoView = () => {};
