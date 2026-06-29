import { useState, useCallback } from 'react';
import Form from './components/Form';

function App() {
  const [darkTheme, setDarkTheme] = useState(false);

  const handleInitTheme = useCallback((isDark: boolean) => {
    setDarkTheme(isDark);
    document.body.className = isDark ? 'dark' : 'light';
  }, []);

  return (
    <div className={darkTheme ? 'dark' : 'light'}>
      <Form onInitTheme={handleInitTheme} />
    </div>
  );
}

export default App;
